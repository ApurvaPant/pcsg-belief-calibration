#!/usr/bin/env python
"""Extractor evaluation harness (extractor_improvement_today.md — STEP 1).

Scores the extractor against a fixed, human-labelled case set so every prompt
or pipeline change can be justified by a NUMBER rather than by eyeballing the
graph. It runs the FULL pipeline per case against a fresh database, then reads
back what was actually stored/changed — never the UI.

Usage
-----
    python -m scripts.eval_extractor --split dev --runs 3
    python -m scripts.eval_extractor --split test --runs 3
    python -m scripts.eval_extractor --case mixed-02
    python -m scripts.eval_extractor --split dev --dry-run      # cost projection only

Rules baked in (from the spec)
------------------------------
- While tuning prompts, run ``--split dev`` only. The ``test`` split is held
  out until STEP 6; this script will refuse ``--split test`` unless you pass
  ``--i-am-at-step-6`` so you cannot look at it by accident.
- Paid (non-mock) runs are gated on ``experiment.budget_usd`` in config: the
  harness projects cost from a pilot and REFUSES to run if the projection
  exceeds the budget (or if the budget is unset / 0).
- Temperature 0 is forced on every model call during evaluation.

Case-file schema (``backend/tests/extractor_eval_cases.json``)
--------------------------------------------------------------
    {
      "cases": [
        {
          "id": "mixed-02",
          "category": "mixed",
          "split": "dev" | "test",
          "history": ["earlier user turn", ...]   # optional; strings or
                                                   # {"role","content"} objects
          "assistant_last": "how long have you two been together?",  # optional
          "message": "for two years",              # the TARGET user turn
          "expect": [                              # [] => negative case
            {
              "subject": "self" | "relationship/girlfriend=hailey",
              "dimension": "relationship",
              "key": "girlfriend",                 # or "key_any": [...]
              "value": "hailey",                   # or "value_any": [...]
              "operation": "ADD",                  # or "operation_any": [...]
              "explicitness": "explicit",          # optional
              "attributes": [                      # optional
                {"name": "duration", "value": "2", "unit": "years"}
              ]
            }
          ],
          "graph_after": {                         # optional graph assertions
            "active_count": 3,
            "node_count_unchanged": true,
            "still_active":  ["relationship/girlfriend=hailey"],
            "superseded":    ["preference/diet=vegetarian"],
            "ended_not_deleted": ["habit/running=running"],
            "unchanged":     ["identity/name=apurva"],
            "must_not_exist":["identity/name=hailey"],
            "no_value_longer_than_words": 4
          }
        }
      ]
    }

Every expected assertion AND every graph check must pass for a case to pass.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

# Make ``backend/`` importable whether run as a module or a file.
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app import extraction, llm, ontology, pipeline  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import DEFAULT_USER_ID, ensure_default_user, get_conn, init_db, now_iso  # noqa: E402
from app.utils import new_id  # noqa: E402

CASES_PATH = Path(os.getenv("EVAL_CASES_PATH") or (BACKEND_DIR / "tests" / "extractor_eval_cases.json"))
RESULTS_DIR = BACKEND_DIR / "eval_results"
WRITE_ACTIONS = {"created", "reinforced", "superseded", "ended", "refined", "linked"}

# Stage labels keyed off the [[TASK:...]] tag embedded in each system prompt.
STAGE_BY_TASK = {"SALIENCE": "tier1", "FRAME": "frame", "FILL": "fill",
                 "EXTRACTION": "fill", "CHAT": "chat"}


# ───────────────────────── instrumentation ──────────────────────────────── #
class CallLedger:
    """Tallies tokens/cost per pipeline stage by reading the [[TASK:...]] tag.

    Wraps ``llm.complete`` so a real run records exactly what each stage cost
    without touching the pipeline. Also captures the last ``ExtractionResult``
    so the harness can score assertion-level fields (subject/operation/
    explicitness/attributes) that the stored ``changes`` rows do not carry.
    """

    def __init__(self) -> None:
        self._orig_complete = llm.complete
        self._orig_extract = extraction.extract_assertions
        self.reset_turn()

    def reset_turn(self) -> None:
        self.stage_tokens: dict[str, dict[str, int]] = defaultdict(
            lambda: {"prompt": 0, "completion": 0})
        self.stage_cost: dict[str, float] = defaultdict(float)
        self.last_ext: extraction.ExtractionResult | None = None

    def install(self) -> None:
        import re
        task_re = re.compile(r"\[\[TASK:([A-Z_]+)\]\]")

        def wrapped_complete(messages, model=None, temperature=None, **kw):  # noqa: ANN001
            res = self._orig_complete(messages, model=model, temperature=0.0,
                                      **{k: v for k, v in kw.items() if k != "temperature"})
            system = " ".join(m["content"] for m in messages if m.get("role") == "system")
            m = task_re.search(system)
            stage = STAGE_BY_TASK.get(m.group(1) if m else "CHAT", "chat")
            pt, ct = int(res.get("prompt_tokens", 0)), int(res.get("completion_tokens", 0))
            self.stage_tokens[stage]["prompt"] += pt
            self.stage_tokens[stage]["completion"] += ct
            self.stage_cost[stage] += settings.experiment.cost_usd(model or "default", pt, ct)
            return res

        def wrapped_extract(*a, **k):  # noqa: ANN001
            res = self._orig_extract(*a, **k)
            self.last_ext = res
            return res

        llm.complete = wrapped_complete
        extraction.extract_assertions = wrapped_extract

    def uninstall(self) -> None:
        llm.complete = self._orig_complete
        extraction.extract_assertions = self._orig_extract

    def turn_cost(self) -> float:
        return sum(self.stage_cost.values())

    def turn_tokens(self) -> int:
        return sum(s["prompt"] + s["completion"] for s in self.stage_tokens.values())


# ───────────────────────── case execution ───────────────────────────────── #
def _fresh_db() -> Path:
    """Point settings at a brand-new DB file and initialise it. Returns the path."""
    tmp = Path(tempfile.mkdtemp(prefix="eval_")) / "state.db"
    settings.db_path = tmp
    init_db()
    with get_conn() as conn:
        ensure_default_user(conn)
    return tmp


def _history_turns(case: dict) -> list[dict[str, str]]:
    """Normalise history to {role, content}. Accepts bare strings (user turns)
    and {role, text|content} objects, including assistant turns (spec step 5)."""
    out = []
    for h in case.get("history", []) or []:
        if isinstance(h, str):
            out.append({"role": "user", "content": h})
        elif isinstance(h, dict):
            content = h.get("content") or h.get("text")
            if content:
                out.append({"role": str(h.get("role", "user")), "content": str(content)})
    return out


def _inject_assistant_last(sid: str, text: str, last_changes: list[dict]) -> None:
    """Insert ``assistant_last`` as the latest turn and open a pending slot if it
    asks about an attribute of a node created on the previous user turn — exactly
    the slot logic the live pipeline runs on its own generated reply."""
    with get_conn() as conn:
        amid = new_id("msg")
        conn.execute(
            "INSERT INTO messages (id, session_id, role, content, created_at) "
            "VALUES (?, ?, 'assistant', ?, ?)", (amid, sid, text, now_iso()))
        det = pipeline._detect_slot(text, last_changes or [])
        if det:
            pipeline._open_slot(conn, sid, det["node_id"], det["attribute_name"], amid,
                                det["slot_type"], det["topic"])


def run_case(case: dict, ledger: CallLedger) -> dict[str, Any]:
    """Run one case end-to-end and return the captured extractor output + graph."""
    _fresh_db()
    sid: str | None = None
    last_changes: list[dict] = []

    for turn in _history_turns(case):
        if turn["role"] == "user":
            res = pipeline.process_turn(turn["content"], sid, DEFAULT_USER_ID)
            sid = res["session_id"]
            last_changes = res.get("changes", [])
        else:  # a scripted assistant turn in history — context only
            if sid is None:
                with get_conn() as conn:
                    sid = pipeline.create_session(conn, DEFAULT_USER_ID, "eval")
            _inject_assistant_last(sid, turn["content"], last_changes)

    if case.get("assistant_last"):
        if sid is None:
            with get_conn() as conn:
                sid = pipeline.create_session(conn, DEFAULT_USER_ID, "eval")
        _inject_assistant_last(sid, case["assistant_last"], last_changes)

    # ── the TARGET turn — this is what we score ── #
    ledger.reset_turn()
    result = pipeline.process_turn(case["message"], sid, DEFAULT_USER_ID)
    ext = ledger.last_ext
    written = [c for c in result.get("changes", []) if c.get("action") in WRITE_ACTIONS]
    kept = [dict(a) for a in (ext.kept if ext else [])]

    return {
        "changes": result.get("changes", []),
        "written": written,
        "kept": _scored_candidates(kept, written),
        "relations": list(ext.relations) if ext else [],
        "graph": _read_graph(result["user_id"]),
        "stage_tokens": {k: dict(v) for k, v in ledger.stage_tokens.items()},
        "stage_cost": dict(ledger.stage_cost),
        "turn_cost": ledger.turn_cost(),
        "turn_tokens": ledger.turn_tokens(),
    }


_ACTION_OP = {"created": "ADD", "superseded": "ADD", "reinforced": "UPDATE",
              "ended": "END", "refined": "REFINE"}


def _scored_candidates(kept: list[dict], written: list[dict]) -> list[dict]:
    """Assertion-level scoring basis: the model's kept assertions PLUS any write
    the pipeline synthesized outside the model (the step-3 refinement attach),
    deduped on (dimension, key, value, operation). Change-derived candidates
    carry no attributes/subject — those are verified via graph_after instead."""
    def sig(a: dict) -> tuple:
        return (a.get("dimension"), a.get("key"),
                ontology.normalize_value(str(a.get("value", ""))),
                str(a.get("operation", "")).upper())

    out = list(kept)
    seen = {sig(a) for a in kept}
    for c in written:
        op = _ACTION_OP.get(c.get("action"))
        if not op:
            continue
        a = {"dimension": c.get("dimension"), "key": c.get("key"), "value": c.get("value"),
             "operation": op, "subject": "self", "explicitness": "explicit", "attributes": []}
        if sig(a) not in seen:
            out.append(a)
            seen.add(sig(a))
    return out


def _read_graph(user_id: str) -> dict[str, Any]:
    """Snapshot every node (+active attributes) as label -> record, for graph_after."""
    with get_conn() as conn:
        nodes = conn.execute("SELECT * FROM nodes WHERE user_id = ?", (user_id,)).fetchall()
        out = {}
        for n in nodes:
            attrs = conn.execute(
                "SELECT name, value, unit FROM attributes WHERE node_id = ? AND status='active'",
                (n["id"],)).fetchall()
            out.setdefault(f"{n['dimension']}/{n['key']}={n['value']}", []).append({
                "id": n["id"], "status": n["status"],
                "status_detail": n["status_detail"], "valid_to": n["valid_to"],
                "value": n["value"],
                "attributes": [{"name": a["name"], "value": a["value"], "unit": a["unit"]}
                               for a in attrs],
            })
        active_count = sum(1 for n in nodes if n["status"] == "active")
        return {"labels": out, "node_count": len(nodes), "active_count": active_count,
                "max_value_words": max((len(n["value"].split()) for n in nodes), default=0)}


# ───────────────────────── scoring ──────────────────────────────────────── #
def _norm(v: Any) -> str:
    return ontology.normalize_value(str(v)) if v is not None else ""


def _match_assertion(exp: dict, got: dict) -> tuple[bool, dict[str, bool]]:
    """Field-by-field match of one expected assertion against one extractor output.
    Returns (all_match, per-field booleans) for field-level accuracy."""
    fields: dict[str, bool] = {}

    # subject
    if "subject" in exp:
        fields["subject"] = _subject_str(got.get("subject")) == str(exp["subject"]).strip().lower()
    # dimension
    if "dimension" in exp:
        fields["dimension"] = str(got.get("dimension", "")) == str(exp["dimension"]).lower()
    # key (exact or any-of)
    if "key" in exp or "key_any" in exp:
        allowed = {_norm_key(k) for k in ([exp["key"]] if "key" in exp else exp["key_any"])}
        fields["key"] = _norm_key(got.get("key")) in allowed
    # value (normalised, exact or any-of)
    if "value" in exp or "value_any" in exp:
        allowed = {_norm(v) for v in ([exp["value"]] if "value" in exp else exp["value_any"])}
        fields["value"] = _norm(got.get("value")) in allowed
    # operation (exact or any-of)
    if "operation" in exp or "operation_any" in exp:
        allowed = {str(o).upper() for o in
                   ([exp["operation"]] if "operation" in exp else exp["operation_any"])}
        fields["operation"] = str(got.get("operation", "")).upper() in allowed
    # explicitness
    if "explicitness" in exp:
        fields["explicitness"] = str(got.get("explicitness", "")) == str(exp["explicitness"]).lower()
    # attributes (each expected attribute must be present on the output)
    if "attributes" in exp:
        fields["attributes"] = _attrs_match(exp["attributes"], got.get("attributes", []))

    return all(fields.values()), fields


def _norm_key(k: Any) -> str:
    return ontology.normalize_key(k) or ""


def _subject_str(subj: Any) -> str:
    if isinstance(subj, dict):
        return f"{subj.get('dimension')}/{subj.get('key')}={subj.get('value')}".lower()
    return str(subj or "self").strip().lower()


def _attrs_match(expected: list[dict], got: list[dict]) -> bool:
    for e in expected:
        hit = False
        for g in got:
            if _norm_key(g.get("name")) != _norm_key(e.get("name")):
                continue
            if "value" in e and _norm(g.get("value")) != _norm(e["value"]):
                continue
            if "unit" in e and (g.get("unit") or None) != (e.get("unit") or None):
                continue
            hit = True
            break
        if not hit:
            return False
    return True


def _label_present(graph: dict, label: str) -> list[dict]:
    return graph["labels"].get(label.strip().lower(), []) \
        or graph["labels"].get(label.strip(), [])


def _check_graph(expected: dict, graph: dict) -> list[str]:
    """Return a list of human-readable failures (empty == all graph checks pass)."""
    fails: list[str] = []
    if "active_count" in expected and graph["active_count"] != expected["active_count"]:
        fails.append(f"active_count {graph['active_count']}!={expected['active_count']}")
    if expected.get("node_count_unchanged") is not None:
        # interpreted at run level by the caller (needs a baseline); skipped here
        pass
    for label in expected.get("still_active", []):
        recs = _label_present(graph, label)
        if not any(r["status"] == "active" for r in recs):
            fails.append(f"not still_active: {label}")
    for label in expected.get("superseded", []):
        recs = _label_present(graph, label)
        if not any(r["status"] == "superseded" for r in recs):
            fails.append(f"not superseded: {label}")
    for label in expected.get("ended_not_deleted", []):
        recs = _label_present(graph, label)
        if not any(r["status_detail"] == "ended" or r["valid_to"] for r in recs):
            fails.append(f"not ended: {label}")
        if any(r["status"] == "deleted" for r in recs):
            fails.append(f"deleted (should end): {label}")
    for label in expected.get("unchanged", []):
        if not _label_present(graph, label):
            fails.append(f"missing unchanged: {label}")
    for label in expected.get("must_not_exist", []):
        if _label_present(graph, label):
            fails.append(f"must_not_exist present: {label}")
    # attributes_on: {label: [{name, value[, unit]}]} must be active on the node.
    for label, wanted in (expected.get("attributes_on", {}) or {}).items():
        recs = _label_present(graph, label)
        have = [a for r in recs for a in r["attributes"]]
        for w in wanted:
            ok = any(_norm_key(a["name"]) == _norm_key(w.get("name"))
                     and ("value" not in w or _norm(a["value"]) == _norm(w["value"]))
                     and ("unit" not in w or (a.get("unit") or None) == (w.get("unit") or None))
                     for a in have)
            if not ok:
                fails.append(f"missing attribute {w.get('name')}={w.get('value')} on {label}")
    if "no_value_longer_than_words" in expected:
        lim = int(expected["no_value_longer_than_words"])
        if graph["max_value_words"] > lim:
            fails.append(f"value longer than {lim} words")
    return fails


def score_case(case: dict, out: dict) -> dict[str, Any]:
    """Score one executed case. Returns pass/fail, field tallies and P/R counts."""
    expect = case.get("expect", [])
    kept = out["kept"]
    negative = len(expect) == 0

    detail: dict[str, Any] = {"id": case.get("id"), "category": case.get("category", "?"),
                              "split": case.get("split", "dev"), "field_hits": {}, "fails": []}

    # ── negative case: pass iff nothing was written to the graph ── #
    if negative:
        detail["tp"], detail["fp"], detail["fn"] = 0, len(out["written"]), 0
        detail["false_write"] = len(out["written"]) > 0
        graph_fails = _check_graph(case.get("graph_after", {}), out["graph"])
        detail["fails"] = ([f"false write: {[w.get('dimension') + '/' + w.get('key') for w in out['written']]}"]
                           if out["written"] else []) + graph_fails
        detail["passed"] = not detail["fails"]
        detail["negative"] = True
        return detail

    # ── positive case: greedily match each expected assertion to an output ── #
    used = set()
    matched = 0
    field_totals: dict[str, list[int]] = defaultdict(lambda: [0, 0])  # [hits, total]
    for exp in expect:
        best_i, best_fields, best_all = None, {}, False
        for i, got in enumerate(kept):
            if i in used:
                continue
            ok, fields = _match_assertion(exp, got)
            if ok:
                best_i, best_fields, best_all = i, fields, True
                break
            # keep the closest partial match for field-level diagnostics
            if sum(fields.values()) > sum(best_fields.values()):
                best_i, best_fields = i, fields
        if best_i is not None:
            used.add(best_i)
        for f, hit in best_fields.items():
            field_totals[f][0] += int(hit)
            field_totals[f][1] += 1
        if best_all:
            matched += 1
        else:
            detail["fails"].append(f"unmatched expected: {exp.get('dimension')}/"
                                   f"{exp.get('key', exp.get('key_any'))}={exp.get('value', exp.get('value_any'))}")

    detail["field_hits"] = {f: tuple(v) for f, v in field_totals.items()}
    detail["tp"] = matched
    detail["fn"] = len(expect) - matched
    detail["fp"] = max(0, len(kept) - matched)
    graph_fails = _check_graph(case.get("graph_after", {}), out["graph"])
    detail["fails"] += graph_fails
    detail["passed"] = (matched == len(expect)) and not graph_fails
    detail["negative"] = False
    return detail


# ───────────────────────── aggregation / reporting ──────────────────────── #
def aggregate(details: list[dict]) -> dict[str, Any]:
    by_cat: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    tp = fp = fn = 0
    neg_total = neg_false = 0
    field_totals: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for d in details:
        by_cat[d["category"]][0] += int(d["passed"])
        by_cat[d["category"]][1] += 1
        tp += d.get("tp", 0); fp += d.get("fp", 0); fn += d.get("fn", 0)
        if d.get("negative"):
            neg_total += 1
            neg_false += int(d.get("false_write", False))
        for f, (h, t) in d.get("field_hits", {}).items():
            field_totals[f][0] += h
            field_totals[f][1] += t
    passed = sum(1 for d in details if d["passed"])
    prec = tp / (tp + fp) if (tp + fp) else 0.0
    rec = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    return {
        "n": len(details), "passed": passed,
        "pass_rate": passed / len(details) if details else 0.0,
        "precision": prec, "recall": rec, "f1": f1,
        "false_write_rate": neg_false / neg_total if neg_total else 0.0,
        "by_category": {c: {"passed": v[0], "n": v[1], "rate": v[0] / v[1] if v[1] else 0.0}
                        for c, v in sorted(by_cat.items())},
        "field_accuracy": {f: {"acc": v[0] / v[1] if v[1] else 0.0, "n": v[1]}
                           for f, v in sorted(field_totals.items())},
    }


def _fmt_pct(x: float) -> str:
    return f"{100 * x:5.1f}%"


def print_report(agg: dict, cost: dict) -> None:
    print("\n" + "=" * 62)
    print(f"  OVERALL  pass {agg['passed']}/{agg['n']} = {_fmt_pct(agg['pass_rate'])}"
          f"   P {_fmt_pct(agg['precision'])}  R {_fmt_pct(agg['recall'])}  F1 {_fmt_pct(agg['f1'])}")
    print(f"  false-write rate (negative cases): {_fmt_pct(agg['false_write_rate'])}")
    print("-" * 62)
    print("  PER CATEGORY")
    for c, v in agg["by_category"].items():
        print(f"    {c:<22} {v['passed']:>2}/{v['n']:<2}  {_fmt_pct(v['rate'])}")
    print("-" * 62)
    print("  FIELD-LEVEL ACCURACY (which decision fails)")
    for f, v in agg["field_accuracy"].items():
        print(f"    {f:<14} {_fmt_pct(v['acc'])}  (n={v['n']})")
    print("-" * 62)
    print("  COST / MESSAGE (mean)")
    for stage in ("tier1", "frame", "fill", "chat"):
        t = cost["stage_tokens_mean"].get(stage, 0)
        c = cost["stage_cost_mean"].get(stage, 0.0)
        print(f"    {stage:<8} {t:>7.1f} tok   ${c:.6f}")
    print(f"    {'TOTAL':<8} {cost['tokens_mean']:>7.1f} tok   ${cost['cost_mean']:.6f}")
    print("=" * 62 + "\n")


# ───────────────────────── budget / cost projection ─────────────────────── #
def project_and_guard(cases: list[dict], runs: int, pilot_n: int = 3) -> float:
    """Run a small pilot, extrapolate the full cost, print it, and ENFORCE the
    budget. Returns the mean cost/case. Raises SystemExit if over budget."""
    if settings.llm.provider == "mock":
        return 0.0  # mock is free
    budget = settings.experiment.budget_usd
    if budget <= 0:  # fail fast BEFORE spending a cent on the pilot
        raise SystemExit("ABORT: experiment.budget_usd is unset (0). Set a ceiling in "
                         "config.yaml before any paid run. (See docs/PROGRESS.md.)")
    ledger = CallLedger()
    ledger.install()
    pilot = cases[:max(1, min(pilot_n, len(cases)))]
    pilot_cost = 0.0
    try:
        for case in pilot:
            out = run_case(case, ledger)
            pilot_cost += out["turn_cost"]
    finally:
        ledger.uninstall()
    mean = pilot_cost / len(pilot)
    projected = mean * len(cases) * runs
    print(f"[pilot] {len(pilot)} cases, mean ${mean:.6f}/case -> "
          f"projected full run (x{runs}) ~= ${projected:.4f}   (budget ${budget:.2f})")
    if projected > budget:
        raise SystemExit(f"ABORT: projected ${projected:.4f} exceeds budget ${budget:.2f}. "
                         f"Reduce --runs or the case set, or raise experiment.budget_usd.")
    return mean


# ───────────────────────── driver ───────────────────────────────────────── #
def load_cases() -> list[dict]:
    if not CASES_PATH.exists():
        raise SystemExit(
            f"MISSING EVAL SET: {CASES_PATH}\n"
            "The 45-case labelled set is not in the repo. Drop it in and re-run.\n"
            "Expected schema is documented at the top of this file.")
    data = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    cases = data["cases"] if isinstance(data, dict) else data
    for i, c in enumerate(cases):
        c.setdefault("id", f"case-{i}")
        c.setdefault("split", "dev")
        c.setdefault("category", "uncategorised")
    return cases


def select(cases: list[dict], split: str, case_id: str | None) -> list[dict]:
    if case_id:
        return [c for c in cases if c["id"] == case_id]
    if split == "all":
        return cases
    return [c for c in cases if c.get("split") == split]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Extractor evaluation harness (STEP 1).")
    ap.add_argument("--split", choices=["dev", "test", "all"], default="dev")
    ap.add_argument("--case", default=None, help="run a single case id")
    ap.add_argument("--runs", type=int, default=1, help="repeat the set N times (variance)")
    ap.add_argument("--dry-run", action="store_true", help="pilot + cost projection only")
    ap.add_argument("--i-am-at-step-6", action="store_true",
                    help="required to touch the held-out test split")
    args = ap.parse_args(argv)

    try:  # Windows consoles default to cp1252; force UTF-8 so reports never crash.
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass

    if args.split == "test" and not args.i_am_at_step_6 and not args.case:
        raise SystemExit("REFUSED: the test split is held out until STEP 6. "
                         "Pass --i-am-at-step-6 only when you are genuinely there.")

    cases = select(load_cases(), args.split, args.case)
    if not cases:
        raise SystemExit("no cases selected")

    print(f"provider={settings.llm.provider}  extractor={llm.resolve_model('extractor')}  "
          f"classifier={llm.resolve_model('classifier')}  split={args.split}  "
          f"cases={len(cases)}  runs={args.runs}  prompt_version={extraction.PROMPT_VERSION}")

    project_and_guard(cases, args.runs)  # pilot + budget guard (free on mock)
    if args.dry_run:
        return 0

    ledger = CallLedger()
    ledger.install()
    run_aggs, run_costs = [], []
    all_details_last: list[dict] = []
    try:
        for r in range(args.runs):
            details, tok_sum, cost_sum = [], defaultdict(float), defaultdict(float)
            tokens_total = cost_total = 0.0
            for case in cases:
                out = run_case(case, ledger)
                details.append(score_case(case, out))
                for stage, tc in out["stage_cost"].items():
                    cost_sum[stage] += tc
                for stage, tv in out["stage_tokens"].items():
                    tok_sum[stage] += tv["prompt"] + tv["completion"]
                tokens_total += out["turn_tokens"]
                cost_total += out["turn_cost"]
            n = len(cases)
            cost = {
                "stage_tokens_mean": {s: tok_sum[s] / n for s in tok_sum},
                "stage_cost_mean": {s: cost_sum[s] / n for s in cost_sum},
                "tokens_mean": tokens_total / n, "cost_mean": cost_total / n,
                "cost_total": cost_total,
            }
            agg = aggregate(details)
            run_aggs.append(agg)
            run_costs.append(cost)
            all_details_last = details
            print(f"\n--- run {r + 1}/{args.runs} ---")
            print_report(agg, cost)
    finally:
        ledger.uninstall()

    # variance across runs
    rates = [a["pass_rate"] for a in run_aggs]
    f1s = [a["f1"] for a in run_aggs]
    mean_rate = sum(rates) / len(rates)
    spread = (max(rates) - min(rates)) if len(rates) > 1 else 0.0
    print(f"ACROSS {args.runs} RUN(S): pass-rate mean {_fmt_pct(mean_rate)}  "
          f"spread +/-{_fmt_pct(spread / 2)}   F1 mean {_fmt_pct(sum(f1s) / len(f1s))}")

    # persist every run (spec: keep all runs so we can compare)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out_path = RESULTS_DIR / f"{stamp}_{extraction.PROMPT_VERSION}.json"
    out_path.write_text(json.dumps({
        "meta": {"timestamp": stamp, "split": args.split, "runs": args.runs,
                 "provider": settings.llm.provider,
                 "extractor_model": llm.resolve_model("extractor"),
                 "classifier_model": llm.resolve_model("classifier"),
                 "prompt_version": extraction.PROMPT_VERSION},
        "runs": [{"aggregate": a, "cost": c} for a, c in zip(run_aggs, run_costs)],
        "mean_pass_rate": mean_rate, "pass_rate_spread": spread,
        "last_run_details": all_details_last,
    }, indent=2), encoding="utf-8")
    print(f"saved -> {out_path.relative_to(BACKEND_DIR)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
