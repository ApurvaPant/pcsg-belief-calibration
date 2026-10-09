"""Build results/<run_id>/dashboard.json — the single data contract (STEP 1-2).

Reads ONLY a run's results artefacts (config/calibration/abstention/cost_curve/
failures/persona_run/labelled/truth). Computes the new offline series (token growth
vs conversation length via tiktoken; gate funnel) with NO model calls. Validated
against pcsg.viz.schema.
"""
from __future__ import annotations

import json
import pathlib
import statistics as st

from . import schema

_WRITE = {"created", "reinforced", "ended", "refined", "linked", "superseded"}


def _tok():
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        return lambda s: len(enc.encode(s or "")), "tiktoken/cl100k_base"
    except Exception:  # noqa: BLE001
        return lambda s: max(1, len(s or "") // 4), "approx(chars/4)"


def _read_jsonl(p):
    return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


def _load(p):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _iqr(xs):
    xs = sorted(xs)
    if not xs:
        return [0, 0]
    if len(xs) < 4:
        return [xs[0], xs[-1]]
    q1 = xs[int(0.25 * (len(xs) - 1))]
    q3 = xs[int(0.75 * (len(xs) - 1))]
    return [q1, q3]


def _token_growth(logs_by_persona, test_pids, tok):
    """Raw-history tokens vs PCSG state-block tokens at turns 5/10/15/20/25."""
    out = []
    for n in schema.TOKEN_GROWTH_TURNS:
        raws, pcsgs = [], []
        for pid in test_pids:
            turns = logs_by_persona.get(pid, [])[:n]
            if not turns:
                continue
            # raw history = cumulative user text (x2 to approximate assistant turns)
            raw = sum(tok(t["text"]) for t in turns) * 2
            # PCSG state block = distinct facts created/reinforced so far, one line each
            facts = {}
            for t in turns:
                for c in t.get("changes", []):
                    if c.get("action") in _WRITE and c.get("key"):
                        facts[(c["dimension"], c["key"])] = c["value"]
            block = "Known about the user so far:\n" + "\n".join(
                f"- {d}.{k} = {v}" for (d, k), v in facts.items())
            raws.append(raw)
            pcsgs.append(tok(block))
        out.append({"turn": n,
                    "raw": {"mean": round(st.mean(raws), 1) if raws else 0, "iqr": _iqr(raws)},
                    "pcsg": {"mean": round(st.mean(pcsgs), 1) if pcsgs else 0, "iqr": _iqr(pcsgs)}})
    return out


def _gate_funnel(logs):
    """Classify each replayed message by where it would stop under nominal
    thresholds: tier0 (non-salient), tier1 (salient, no structure), structured."""
    tier0 = tier1 = structured = 0
    for m in logs:
        wrote = any(c.get("action") in _WRITE for c in m.get("changes", []))
        if wrote:
            structured += 1
        elif (m.get("salience") or 0) >= 50:
            tier1 += 1
        else:
            tier0 += 1
    per = 300  # nominal tokens/message at each stage (dry-run estimate)
    stages = [
        {"stage": "tier0_gate", "count": tier0, "tokens": tier0 * 20, "cost": 0.0},
        {"stage": "tier1_salience", "count": tier1, "tokens": tier1 * 60, "cost": 0.0},
        {"stage": "structured_extraction", "count": structured, "tokens": structured * per, "cost": 0.0},
    ]
    return {"stages": stages, "total_messages": len(logs)}


def _calibration_section(cal, labelled):
    if not cal:
        return {}
    # confidence histograms per signal from labelled records
    hists = {s: {} for s in ("verbalized", "self_consistency", "evidence")}
    for r in labelled:
        for s in hists:
            v = r.get(s)
            if v is None:
                continue
            b = min(9, int(v * 10))
            hists[s][b] = hists[s].get(b, 0) + 1
    out = {"signals": {}}
    for sig, recs in cal.get("by_signal_recalibrator", {}).items():
        rel = cal.get("reliability", {}).get(sig, {})
        out["signals"][sig] = {
            "recalibrators": {rec: {"ece": m["ece"], "brier": m["brier"], "auroc": m["auroc"],
                                    "ci": m.get("ci", {})} for rec, m in recs.items()},
            "reliability": {"before": rel.get("before", []), "after": rel.get("after", [])},
            "histogram": [{"bin": b / 10, "count": hists[sig].get(b, 0)} for b in range(10)],
        }
    return out


def _kpi(cal, cost, abn, tg, baselines):
    k = {}
    if tg:
        last = tg[-1]
        full, pcsg = last["raw"]["mean"], last["pcsg"]["mean"]
        k["tokens_per_q_full"] = full
        k["tokens_per_q_pcsg"] = pcsg
        k["pct_saved"] = round(100 * (1 - pcsg / full), 1) if full else None
    if cal:
        v = cal.get("by_signal_recalibrator", {}).get("verbalized", {})
        if v:
            k["ece_before"] = v.get("raw", {}).get("ece")
            k["ece_after"] = min((v.get(r, {}).get("ece", 1) for r in ("temperature", "platt", "isotonic")), default=None)
    k["baselines"] = {b["system"]: b.get("accuracy") for b in baselines}
    return k


def build_dashboard(run_dir: pathlib.Path) -> dict:
    tok, tok_name = _tok()
    cfg = _load(run_dir / "config.json") or {}
    cal = _load(run_dir / "calibration.json")
    abn = _load(run_dir / "abstention.json")
    cost = _load(run_dir / "cost_curve.json")
    fails = _load(run_dir / "failures.json") or {}
    baselines_raw = _load(run_dir / "baselines.json") or {}
    logs = _read_jsonl(run_dir / "persona_run.jsonl")
    labelled = _read_jsonl(run_dir / "labelled.jsonl")
    truth = _load(run_dir / "truth.json") or {}

    logs_by_persona, splits = {}, {}
    for m in logs:
        logs_by_persona.setdefault(m["persona"], []).append(m)
        splits[m["persona"]] = m.get("split", "fit")
    test_pids = [p for p, s in splits.items() if s == "test"]

    # baselines: list with fixed colours (dry-run may be empty)
    baselines = []
    for sysname, color in schema.SYSTEM_COLORS.items():
        b = baselines_raw.get(sysname)
        if b:
            baselines.append({"system": sysname, "color": color, **b})

    tg = _token_growth(logs_by_persona, test_pids, tok)
    # persona replay (first 3 test personas): per-turn created facts + correctness + cum tokens
    from pcsg.analysis.labelling import label_one
    replay = []
    for pid in test_pids[:3]:
        turns_out, cum_full, cum_pcsg, facts = [], 0, 0, {}
        for i, m in enumerate(logs_by_persona.get(pid, []), 1):
            cum_full += tok(m["text"]) * 2
            asserts = []
            for c in m.get("changes", []):
                if c.get("action") in _WRITE and c.get("key"):
                    facts[(c["dimension"], c["key"])] = c["value"]
                    ok, _ = label_one({"dimension": c["dimension"], "value": c["value"]}, truth.get(str(pid), truth.get(pid, [])))
                    asserts.append({"dimension": c["dimension"], "key": c["key"], "value": c["value"],
                                    "confidence": 0.9, "correct": ok})
            cum_pcsg = tok("\n".join(f"{d}.{k}={v}" for (d, k), v in facts.items()))
            turns_out.append({"turn": i, "text": m["text"], "assertions": asserts,
                              "cum_tokens_full": cum_full, "cum_tokens_pcsg": cum_pcsg})
        replay.append({"persona": pid, "turns": turns_out})

    dash = {
        "meta": {
            "run_id": cfg.get("run_id", run_dir.name), "git_sha": cfg.get("git_sha", "unknown"),
            "tag": cfg.get("tag", ""), "mode": cfg.get("mode", "dry-run"),
            "date": cfg.get("run_id", run_dir.name), "models": cfg.get("models", {}),
            "tokenizer": tok_name,
            "sample_sizes": {"personas": len(logs_by_persona), "test_personas": len(test_pids),
                             "labelled_assertions": len(labelled),
                             "failed_extractions": fails.get("failed_extractions", 0)},
        },
        "watermark": "DRY RUN — SYNTHETIC DATA, NOT RESULTS" if cfg.get("mode") != "live" else None,
        "kpi": _kpi(cal, cost, abn, tg, baselines),
        "baselines": baselines,
        "token_growth": tg,
        "gate_funnel": _gate_funnel(logs),
        "cost_curve": cost or {"cells": []},
        "calibration": _calibration_section(cal, labelled),
        "abstention": abn or {"sweep": {}},
        "persona_replay": replay,
    }
    return dash


def write_dashboard(run_dir: pathlib.Path) -> tuple[pathlib.Path, list[str]]:
    dash = build_dashboard(run_dir)
    errs = schema.validate(dash)
    out = run_dir / "dashboard.json"
    out.write_text(json.dumps(dash, indent=2), encoding="utf-8")
    return out, errs
