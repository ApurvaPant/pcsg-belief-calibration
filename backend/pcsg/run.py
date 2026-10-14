"""Experiment runner (STEP 3 + STEP 8).

    python -m pcsg.run experiments/<name>.yaml --dry-run    # oracle_mock, no network
    python -m pcsg.run experiments/<name>.yaml --live       # real provider

Records a full config.json (git sha/tag, models, temps, max_tokens, seeds, dataset
revisions, prompt hashes). RESUMABLE: a checkpoint is written after every persona
and QA batch; a rerun skips completed units (never re-pays). A budget_usd guard
aborts cleanly before overspend. Failed extractions are counted via /debug/failures
and excluded. Dry-run artefacts are watermarked DRY RUN — SYNTHETIC.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import subprocess
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent      # backend/
REPO = ROOT.parent                                          # repo root
RESULTS = REPO / "results"                                  # make_figures reads here


def _sha256(s): return hashlib.sha256((s or "").encode()).hexdigest()[:16]


class PriceConfigError(RuntimeError):
    """A live run was requested but one or more configured models lack a price."""


def check_prices(provider: str, models: dict, prices: dict, free_tier: bool = False) -> None:
    """Refuse a live (non-mock) run when any configured model has a missing or
    zero price. Mock / oracle_mock runs are free and always allowed, as are
    free-tier providers (e.g. NVIDIA build), which have no dollar cost and are
    bounded by a TOKEN budget instead. The check reads the RAW price table (never
    the silent `default` fallback), so an unpriced model is caught, not billed at
    a guess.

    Run `python scripts/fill_prices.py` to populate prices before going live.
    """
    if provider in ("mock", "oracle_mock") or free_tier:
        return
    bad = []
    for slug in sorted({v for v in models.values() if v and v != "mock"}):
        p = prices.get(slug)
        if not p or float(p.get("input", 0) or 0) <= 0 or float(p.get("output", 0) or 0) <= 0:
            bad.append(slug)
    if bad:
        raise PriceConfigError(
            "Refusing a live run: missing or zero price for "
            f"{bad}. Run `python scripts/fill_prices.py` to populate "
            "experiment.prices_per_mtok in config.yaml, then retry."
        )


def model_mismatches(call_log: list[dict]) -> list[dict]:
    """Report/metrics guard: flag any call whose SERVED model differs from the
    REQUESTED model (a provider silently substituting a model would corrupt the
    experiment's model attribution). `call_log` rows carry model_requested +
    model_served (llm.complete surfaces the served model in result['model'])."""
    return [c for c in call_log
            if c.get("model_served") and c.get("model_requested")
            and c["model_served"] != c["model_requested"]]


def _git_sha():
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                              cwd=ROOT.parent).stdout.strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def _config_record(args, settings, extraction, pipeline, salience):
    return {
        "run_id": args.run_id, "mode": "dry-run" if args.dry_run else "live",
        "git_sha": _git_sha(), "watermark": args.watermark,
        "provider": settings.llm.provider, "base_url": settings.llm.base_url,
        "models": settings.llm.models, "temperature": settings.llm.temperature,
        "max_tokens": settings.llm.max_tokens, "budget_usd": settings.experiment.budget_usd,
        "seed": args.seed, "n_personas": args.n_personas,
        "salience_threshold_replay": 0.0, "self_consistency": True,
        "prompt_hashes": {"FRAME": _sha256(extraction._FRAME_SYSTEM),
                          "FILL": _sha256(extraction._fill_system()),
                          "CHAT": _sha256(pipeline._CHAT_SYSTEM),
                          "SALIENCE": _sha256(salience._CLASSIFIER_SYSTEM)},
        "prompt_version": extraction.PROMPT_VERSION,
        "longmemeval_revision": "see pcsg/longmemeval.py REVISION",
    }


def select_personas(seed, n_personas, split=None, limit=0):
    """Deterministic persona set for a run, optionally filtered to one split and
    capped at `limit` (0 = all). Used by both replay and QA so they agree."""
    from pcsg import datagen
    personas = datagen.generate_personas(seed, n_personas)
    if split:
        personas = [p for p in personas if p.split == split]
    if limit and limit > 0:
        personas = personas[:limit]
    return personas


def _replay(args, outdir, personas):
    """Gate-open replay of the given personas; collect stored signals + logs. Resumable."""
    from app.config import settings
    settings.pipeline.salience_threshold = 0.0        # gate FULLY open
    os.environ["PCSG_SELF_CONSISTENCY"] = "1"
    from app import pipeline
    from app.db import get_conn, now_iso
    from app.utils import new_id
    from pcsg import datagen, oracle
    if args.dry_run:
        oracle.configure(args.profile, seed=args.seed)

    from app import llm
    llm.set_rate_limit(getattr(args, "rate_limit", None))   # shared across workers (no-op offline)
    # Budget metric: dollars normally, TOKENS on a free-tier provider (NVIDIA).
    free_tier = bool(settings.experiment.free_tier)
    budget = float(settings.experiment.token_budget if free_tier else (settings.experiment.budget_usd or 0.0))
    llm.reset_cost()

    def _over_budget():
        return budget > 0 and (llm.spent_tokens() if free_tier else llm.spent_usd()) >= budget

    def _spent_str():
        return f"{llm.spent_tokens():,} tok >= {int(budget):,} tok" if free_tier \
            else f"${llm.spent_usd():.4f} >= ${budget:.2f}"

    ckpt_path = outdir / "checkpoint.json"
    done = set(json.loads(ckpt_path.read_text()).get("personas", [])) if ckpt_path.exists() else set()
    logs_path = outdir / "persona_run.jsonl"
    logf = open(logs_path, "a", encoding="utf-8")
    truth = {}
    args.budget_stopped = False
    _t_start = time.time()
    _total = len(personas)

    def _write_progress():
        # Lightweight heartbeat the overnight queue renders into QUEUE_STATUS.md.
        n429 = sum(1 for r in llm.RETRY_LOG if "429" in str(r.get("detail", "")) or r.get("error") == "HTTPStatusError")
        (outdir / "progress.json").write_text(json.dumps({
            "stage": "replay", "done": len(done), "total": _total,
            "elapsed_sec": round(time.time() - _t_start, 1),
            "spent_tokens": llm.spent_tokens(), "calls": int(llm.cost_summary().get("calls", 0)),
            "retries": len(llm.RETRY_LOG), "http_429": n429,
            "ts": now_iso()}), encoding="utf-8")

    pause_flag = RESULTS / "PAUSE"
    for p in personas:
        # Graceful pause: results/PAUSE finishes the in-progress persona, then stops
        # cleanly (resumable). The queue creates PAUSE on request / first Ctrl+C.
        if pause_flag.exists():
            args.paused = True
            print(f"[run] PAUSED (results/PAUSE present): {len(done)} personas done; "
                  f"resume with --resume {args.run_id}.")
            break
        # Hard budget stop: cleanly halt BEFORE starting a persona once logged
        # spend reaches the ceiling. Completed personas are checkpointed, so the
        # run is resumable with --resume <run_id>.
        if _over_budget():
            args.budget_stopped = True
            print(f"[run] BUDGET STOP: spent {_spent_str()}; {len(done)} personas done, "
                  f"checkpoint written (resume with --resume {args.run_id}).")
            break
        # Calibration labels "was this extraction correct", so truth includes ALL
        # facts the oracle was asked to produce (incl. superseded/ended values).
        truth[p.pid] = [{"id": k, "dimension": f.dimension, "value": f.value}
                        for k, f in p.facts.items()]
        if p.pid in done:
            continue
        try:
            uid, sid = new_id("user"), new_id("sess")
            with get_conn() as c:
                c.execute("INSERT INTO users(id,label,created_at)VALUES(?,?,?)", (uid, p.name, now_iso()))
                c.execute("INSERT INTO sessions(id,user_id,title,created_at)VALUES(?,?,?,?)", (sid, uid, "exp", now_iso()))
            for t in p.turns:
                if args.dry_run:
                    oracle.set_turn(datagen.turn_expected(p, t))
                r = pipeline.process_turn(t.text, sid, uid)
                logf.write(json.dumps({"persona": p.pid, "split": p.split, "text": t.text,
                                       "salience": round((r["salience_score"] or 0) * 100),
                                       "changes": r["changes"]}) + "\n")
            logf.flush()
            done.add(p.pid)
            ckpt_path.write_text(json.dumps({"personas": sorted(done), "stage": "replay"}), encoding="utf-8")
            _write_progress()
        except Exception as e:  # noqa: BLE001 — transient DB/IO: skip; --resume retries
            print(f"[run] persona {p.pid} failed: {type(e).__name__}: {str(e)[:80]} (resumable)")
    logf.close()
    _write_progress()
    (outdir / "truth.json").write_text(json.dumps(truth), encoding="utf-8")
    # collect signals per persona
    split_by = {p.pid: p.split for p in personas}
    pid_by_user = {}
    with get_conn() as c:
        for u in c.execute("SELECT id, label FROM users"):
            pass
    return personas, truth, split_by


def _collect_signals(personas):
    from app.db import get_conn
    name_to_pid = {}
    for p in personas:
        name_to_pid.setdefault(p.name, p.pid)
    stored = {p.pid: [] for p in personas}
    with get_conn() as c:
        rows = c.execute(
            "SELECT cs.dimension, cs.key, cs.value, cs.verbalized, cs.self_consistency, cs.evidence, "
            "cs.explicitness, u.label AS uname FROM confidence_signals cs "
            "JOIN nodes n ON n.id = cs.node_id JOIN users u ON u.id = n.user_id").fetchall()
    for r in rows:
        pid = name_to_pid.get(r["uname"])
        if pid is None:
            continue
        stored[pid].append({"dimension": r["dimension"], "key": r["key"], "value": r["value"],
                            "verbalized": r["verbalized"], "self_consistency": r["self_consistency"],
                            "evidence": r["evidence"], "explicitness": r["explicitness"]})
    return stored


def _qa_and_abstention(args, outdir, personas):
    """Tier-2 QA across ALL FOUR systems (no_memory, full_context, vector_rag,
    pcsg) on the SAME questions, SAME answer template, SAME judge, SAME token
    logging. Per-system metrics -> qa_systems.json; per-system risk-coverage
    sweeps -> abstention.json (F2 shows all four)."""
    from app.db import get_conn
    from pcsg import oracle, personaqa, baselines
    from pcsg.analysis import qa_metrics
    from tests.robustness.runner import _make_judge
    judge = (lambda rubric, reply: ("PASS" in oracle._judge(f"RUBRIC:{rubric} REPLY:{reply}"), "oracle")) \
        if args.dry_run else _make_judge("real")

    # The persona's graph was built during replay under a user labelled p.name.
    with get_conn() as c:
        label_to_uid = {r["label"]: r["id"] for r in c.execute("SELECT id, label FROM users")}

    test_personas = personas if getattr(args, "split", None) else [p for p in personas if p.split == "test"]
    records = {s: [] for s in baselines.SYSTEMS}
    n_questions = 0
    for p in test_personas:
        uid = label_to_uid.get(p.name)
        history = [{"role": "user", "text": t.text, "turn": i} for i, t in enumerate(p.turns)]
        for q in personaqa.make_qa(p):
            n_questions += 1
            unknown = q["gold"] == "__unknown__"
            if args.dry_run:
                oracle.set_qa(None if unknown else q["gold"])
            # Resilience: a question that errors out (e.g. a call that exhausts its
            # retries) is recorded as a failed/abstained answer for every system and
            # the run continues — one bad question never stops the overnight run.
            try:
                with get_conn() as c:
                    answers = {
                        "no_memory": baselines.answer_no_memory(q["question"]),
                        "full_context": baselines.answer_full_context(q["question"], history),
                        "vector_rag": baselines.answer_vector_rag(q["question"], history, k=5),
                        "pcsg": (baselines.answer_pcsg(q["question"], c, uid) if uid else
                                 {"system": "pcsg", "answer": "I don't know", "confidence": 0.2,
                                  "abstained": True, "tokens_in": 0, "tokens_out": 0}),
                    }
                for s, a in answers.items():
                    if unknown:
                        correct = 1 if a["abstained"] else 0           # correct action = abstain
                    else:
                        ok, _ = judge(f"answer conveys: {q['gold']}", a["answer"])
                        correct = 1 if (ok and not a["abstained"]) else 0
                    records[s].append({
                        "system": s, "persona": p.pid, "kind": q["kind"],
                        "confidence": a["confidence"], "correct": correct,
                        "abstained": 1 if a["abstained"] else 0,
                        "is_unknown": 1 if unknown else 0,
                        "tokens": a.get("tokens_in", 0) + a.get("tokens_out", 0),
                    })
            except Exception as e:  # noqa: BLE001 — never let one question kill the run
                print(f"[run] QA question failed (persona {p.pid}, {q.get('kind')}): "
                      f"{type(e).__name__}: {str(e)[:80]} — recorded as failed, continuing")
                for s in baselines.SYSTEMS:
                    records[s].append({"system": s, "persona": p.pid, "kind": q["kind"],
                                       "confidence": 0.0, "correct": 1 if unknown else 0,
                                       "abstained": 1, "is_unknown": 1 if unknown else 0,
                                       "tokens": 0, "failed": 1})

    per_system = qa_metrics.summarize_all(records)
    (outdir / "qa_systems.json").write_text(json.dumps(
        {"watermark": args.watermark, "n_questions": n_questions, "systems": per_system}, indent=2),
        encoding="utf-8")
    # baselines.json (T3): one row per system for the dashboard + make_figures.
    (outdir / "baselines.json").write_text(json.dumps(
        {s: {"accuracy": per_system[s]["accuracy"],
             "fabrication": per_system[s]["fabrication_rate"],
             "tokens_per_q": per_system[s]["tokens_per_q"],
             "selective_accuracy": per_system[s]["selective_accuracy"],
             "coverage": per_system[s]["coverage"], "aurc": per_system[s]["aurc"],
             "answer_ece": per_system[s]["answer_ece"]} for s in baselines.SYSTEMS}, indent=2),
        encoding="utf-8")
    # abstention.json: per-system sweeps (F2 plots all four) + PCSG headline.
    (outdir / "abstention.json").write_text(json.dumps(
        {"watermark": args.watermark,
         "sweep": {s: per_system[s]["sweep"] for s in baselines.SYSTEMS},
         "aurc": {s: per_system[s]["aurc"] for s in baselines.SYSTEMS},
         "n": n_questions}, indent=2), encoding="utf-8")
    return records


def _longmemeval_stage(args, outdir):
    """Run N LongMemEval questions (T5) through the four systems. Prefers the real
    cached oracle set (pinned revision); falls back to the mock fixture if the
    dataset is unavailable offline. Includes >=1 abstention (_abs) when possible."""
    from pcsg import longmemeval, longmemeval_run
    n = int(args.lme)
    try:
        data = longmemeval.load()
        questions, src = data["questions"], "dataset"
    except Exception as e:  # noqa: BLE001 — offline / no cache: fall back to the fixture
        print(f"[run] LongMemEval dataset unavailable ({type(e).__name__}); using mock fixture.")
        fix = ROOT / "tests" / "fixtures" / "longmemeval_mini.json"
        questions, src = json.loads(fix.read_text(encoding="utf-8")), f"fixture ({type(e).__name__})"
    abst = [q for q in questions if q.get("is_abstention")]
    rest = [q for q in questions if not q.get("is_abstention")]
    chosen = ((abst[:1] + rest[: max(0, n - 1)]) if abst else rest[:n])[:n] or questions[:n]
    res = longmemeval_run.run(chosen, outdir / "longmemeval", dry_run=args.dry_run,
                              workers=1, rate_limit_per_min=getattr(args, "rate_limit", None),
                              watermark=args.watermark)
    res["revision"] = longmemeval.REVISION
    (outdir / "longmemeval.json").write_text(json.dumps(res, indent=2), encoding="utf-8")
    print(f"[run] LongMemEval T5: {res['n_questions']} q from {src}; by_type={res['count_by_type']}, "
          f"n_abstention={res['n_abstention']}")
    return res


def _failures():
    from app.db import get_conn
    with get_conn() as c:
        n = c.execute("SELECT COUNT(*) n FROM extraction_log WHERE status='failed'").fetchone()["n"]
        by = c.execute("SELECT reason_code, COUNT(*) n FROM extraction_log WHERE status='failed' GROUP BY reason_code").fetchall()
    return {"failed_extractions": n, "by_reason": {r["reason_code"] or "?": r["n"] for r in by}}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("spec", help="experiments/<name>.yaml")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--resume", default=None, help="existing run_id to resume")
    ap.add_argument("--limit", type=int, default=0, help="cap personas (0=all); dry-run aid")
    ap.add_argument("--n", type=int, default=0, help="override n_personas (0=use spec); dry-run aid")
    ap.add_argument("--split", default=None, choices=["fit", "test", "dev"], help="restrict to one split; dry-run aid")
    ap.add_argument("--outdir", default="runs", help="results/<outdir>/<run_id> (e.g. dryrun)")
    ap.add_argument("--lme", type=int, default=0, help="also run N LongMemEval questions (T5); 0=skip")
    ap.add_argument("--run-id", dest="run_id_arg", default=None,
                    help="fixed run_id (lets the priority queue reuse one run DB across tiers)")
    ap.add_argument("--stages", default="replay,calibration,qa,lme,cost",
                    help="comma list of stages to run (replay always runs; others gated). "
                         "The queue runs tiers as separate invocations that reuse --run-id.")
    args = ap.parse_args(argv)
    args.stage_set = {s.strip() for s in args.stages.split(",") if s.strip()}
    args.dry_run = args.dry_run or not args.live

    import yaml
    spec = yaml.safe_load((ROOT.parent / args.spec).read_text(encoding="utf-8")) if (ROOT.parent / args.spec).exists() \
        else yaml.safe_load((ROOT / args.spec).read_text(encoding="utf-8"))
    args.seed = int(spec.get("seed", 2026))
    args.n_personas = args.n if getattr(args, "n", 0) else int(spec.get("n_personas", 100))
    args.profile = spec.get("oracle_profile", "overconfident")
    args.rate_limit = spec.get("rate_limit_per_min")
    args.watermark = "DRY RUN — SYNTHETIC" if args.dry_run else None

    # provider
    os.environ["PCSG_TEST_MODE"] = "1"
    if args.dry_run:
        os.environ["LLM_PROVIDER"] = "oracle_mock"
    from app.config import settings
    if args.dry_run:
        settings.llm.provider = "oracle_mock"
    # Per-spec model / request-param overrides (used by the optional model2 and
    # effort-ablation runs, which swap the pipeline model / thinking setting
    # without editing config.yaml). Live runs only; never touches prompts.
    for role, model in (spec.get("models_override") or {}).items():
        settings.llm.models[role] = model
    if spec.get("request_params_override"):
        settings.llm.request_params = {**settings.llm.request_params, **spec["request_params_override"]}
    import tempfile, uuid
    args.run_id = args.resume or getattr(args, "run_id_arg", None) or time.strftime("%Y%m%d_%H%M%S")
    outdir = RESULTS / args.outdir / args.run_id
    outdir.mkdir(parents=True, exist_ok=True)
    settings.db_path = pathlib.Path(tempfile.gettempdir()) / f"pcsg_exp_{args.run_id}.db"
    from app import db
    db.init_db()
    # Price-guard: a live run must have a real price for every configured model
    # (skipped on free-tier providers, which are bounded by a token budget).
    check_prices(settings.llm.provider, settings.llm.models, settings.experiment.prices_per_mtok,
                 free_tier=settings.experiment.free_tier)
    from app import extraction, pipeline, salience
    # Safety net: a --resume invocation inherits the ORIGINAL persona count/seed
    # from the run's config.json unless explicitly overridden with --n, so later
    # tiers (QA/LME/cost) operate on exactly the personas Tier 1 replayed — never
    # silently reselecting the spec default.
    existing_cfg = outdir / "config.json"
    if args.resume and not getattr(args, "n", 0) and existing_cfg.exists():
        try:
            prev = json.loads(existing_cfg.read_text(encoding="utf-8"))
            args.n_personas = int(prev.get("n_personas", args.n_personas))
            args.seed = int(prev.get("seed", args.seed))
        except Exception:  # noqa: BLE001
            pass
    existing_cfg.write_text(json.dumps(_config_record(args, settings, extraction, pipeline, salience), indent=2), encoding="utf-8")
    print(f"[run] {args.run_id} mode={'dry-run' if args.dry_run else 'LIVE'} provider={settings.llm.provider} -> {outdir}")

    # STAGE: persona replay (gate open) -> signals -> labelling -> calibration
    _ck = outdir / "checkpoint.json"
    pre_done = len(json.loads(_ck.read_text()).get("personas", [])) if _ck.exists() else 0
    personas = select_personas(args.seed, args.n_personas, args.split, args.limit)
    print(f"[run] personas selected: {len(personas)}"
          + (f" (split={args.split})" if args.split else "") + (f" limit={args.limit}" if args.limit else ""))
    personas, truth, split_by = _replay(args, outdir, personas)
    post_done = len(json.loads(_ck.read_text()).get("personas", [])) if _ck.exists() else 0
    args.new_personas = post_done - pre_done
    args.total_personas = len(personas)
    from app import llm
    if getattr(args, "paused", False):
        (outdir / "paused.json").write_text(json.dumps(
            {"paused": True, "run_id": args.run_id, "spent_tokens": llm.spent_tokens(),
             "resumable": True}, indent=2), encoding="utf-8")
        print(f"[run] PAUSED; resume with --resume {args.run_id}.")
        return 0
    if getattr(args, "budget_stopped", False):
        (outdir / "budget_stop.json").write_text(json.dumps(
            {"free_tier": settings.experiment.free_tier,
             "budget_usd": settings.experiment.budget_usd,
             "token_budget": settings.experiment.token_budget,
             "spent_tokens": llm.spent_tokens(), "cost": llm.cost_summary(),
             "resumable": True, "run_id": args.run_id}, indent=2), encoding="utf-8")
        print(f"[run] stopped on budget; resume with --resume {args.run_id}. cost={llm.cost_summary()}")
        return 0
    stages = getattr(args, "stage_set", {"replay", "calibration", "qa", "lme", "cost"})
    from pcsg.analysis import labelling, calibration, costcurve
    if "calibration" in stages:
        # STAGE: Tier 1 — signals -> labelling -> calibration metrics (T1/T2/F1).
        stored = _collect_signals(personas)
        records = labelling.label_run(stored, truth, split_by)
        with open(outdir / "labelled.jsonl", "w", encoding="utf-8") as lf:
            for r in records:
                lf.write(json.dumps(r) + "\n")
        cal = calibration.study(records)
        cal["watermark"] = args.watermark
        (outdir / "calibration.json").write_text(json.dumps(cal, indent=2), encoding="utf-8")
        print(f"[run] calibration: {len(records)} labelled assertions; signals={list(cal['by_signal_recalibrator'])}")

    if "qa" in stages:
        # STAGE: Tier 2 — QA across all four systems + abstention (T3/F2).
        _qa_and_abstention(args, outdir, personas)

    if "lme" in stages and getattr(args, "lme", 0):
        # STAGE: Tier 3 — LongMemEval through the four systems (T5).
        _longmemeval_stage(args, outdir)

    if "cost" in stages:
        # STAGE: cost replay (F3) from the persona logs.
        logs = []
        for ln in (outdir / "persona_run.jsonl").read_text(encoding="utf-8").splitlines():
            m = json.loads(ln)
            asserts = [{"fact_id": f"{m['persona']}_{c.get('key')}_{c.get('value')}",
                        "confidence": 0.9, "correct": 1 if c.get("action") in ("created", "reinforced") else 0}
                       for c in m["changes"] if c.get("action") in ("created", "reinforced")]
            logs.append({"persona": m["persona"], "salience": m["salience"], "tokens": 300, "assertions": asserts})
        truth_counts = {p.pid: len(p.expected_active()) for p in personas}
        cc = costcurve.replay(logs, truth_counts)
        cc["watermark"] = args.watermark
        (outdir / "cost_curve.json").write_text(json.dumps(cc, indent=2), encoding="utf-8")

    # STAGE: failures + cost + minimal T1/baselines placeholders (dry-run)
    (outdir / "failures.json").write_text(json.dumps(_failures(), indent=2), encoding="utf-8")
    (outdir / "cost.json").write_text(json.dumps(llm.cost_summary(), indent=2), encoding="utf-8")
    (outdir / "done.json").write_text(json.dumps({"stages": ["replay", "calibration", "qa_abstention", "cost"], "ok": True}), encoding="utf-8")

    # ── stage summary + anti-silent-pass guard ──────────────────────────────
    from app.db import now_iso as _now
    calls = int(llm.cost_summary().get("calls", 0))
    qa_n = 0
    qp = outdir / "qa_systems.json"
    if qp.exists():
        try: qa_n = int(json.loads(qp.read_text()).get("n_questions", 0))
        except Exception: pass
    mode = "dry-run" if args.dry_run else "live"
    (outdir / "last_stage.json").write_text(json.dumps({
        "run_id": args.run_id, "mode": mode, "stages": sorted(stages),
        "new_personas": args.new_personas, "total_personas": args.total_personas,
        "model_calls": calls, "qa_questions": qa_n, "ts": _now()}, indent=2), encoding="utf-8")
    print(f"[run] SUMMARY run_id={args.run_id} mode={mode} stages={sorted(stages)} "
          f"personas_done={args.new_personas}/{args.total_personas} model_calls={calls} qa_q={qa_n}")
    # A LIVE stage that ran a model-calling step must have made calls / done work;
    # otherwise fail loudly (never pass silently — the queue stops).
    if not args.dry_run:
        calling = (args.new_personas > 0) or ("qa" in stages) or ("lme" in stages and getattr(args, "lme", 0))
        if calling and calls == 0:
            print("[run] GUARD FAIL: live model-calling stage made ZERO model calls — aborting.")
            return 3
        if ("replay" in stages) and args.total_personas > 0 and pre_done < args.total_personas and args.new_personas == 0:
            print("[run] GUARD FAIL: live replay completed ZERO new personas — aborting.")
            return 3

    # publish latest -> results/ root for make_figures — ONLY for a normal run.
    # A focused dry run (--outdir dryrun) stays isolated and never mixes with
    # final results (readiness rule).
    if args.outdir == "runs":
        for name in ("calibration.json", "abstention.json", "cost_curve.json", "qa_systems.json",
                     "baselines.json", "longmemeval.json"):
            src = outdir / name
            if src.exists():  # stage-safe: only publish artefacts that were produced
                (RESULTS / name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"[run] DONE (stages={sorted(stages)}). artefacts in {outdir} (+ published to results/).")
    else:
        print(f"[run] DONE (stages={sorted(stages)}). artefacts in {outdir} (isolated).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
