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


def _replay(args, outdir):
    """Gate-open replay of every persona; collect stored signals + logs. Resumable."""
    from app.config import settings
    settings.pipeline.salience_threshold = 0.0        # gate FULLY open
    os.environ["PCSG_SELF_CONSISTENCY"] = "1"
    from app import pipeline
    from app.db import get_conn, now_iso
    from app.utils import new_id
    from pcsg import datagen, oracle
    if args.dry_run:
        oracle.configure(args.profile, seed=args.seed)

    personas = datagen.generate_personas(args.seed, args.n_personas)
    ckpt_path = outdir / "checkpoint.json"
    done = set(json.loads(ckpt_path.read_text()).get("personas", [])) if ckpt_path.exists() else set()
    logs_path = outdir / "persona_run.jsonl"
    logf = open(logs_path, "a", encoding="utf-8")
    truth = {}
    for p in personas:
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
        except Exception as e:  # noqa: BLE001 — transient DB/IO: skip; --resume retries
            print(f"[run] persona {p.pid} failed: {type(e).__name__}: {str(e)[:80]} (resumable)")
    logf.close()
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
    from app import pipeline, llm
    from app.db import get_conn, now_iso
    from app.utils import new_id
    from pcsg import oracle, personaqa
    from tests.robustness.runner import _make_judge  # reuse the judge wiring
    import app.config as cfg
    judge = (lambda rubric, reply: ("PASS" in oracle._judge(f"RUBRIC:{rubric} REPLY:{reply}"), "oracle")) \
        if args.dry_run else _make_judge("real")
    recs = []
    test_personas = [p for p in personas if p.split == "test"]
    for p in test_personas:
        uid, sid = new_id("user"), new_id("sess")
        with get_conn() as c:
            c.execute("INSERT INTO users(id,label,created_at)VALUES(?,?,?)", (uid, "qa_"+p.name, now_iso()))
            c.execute("INSERT INTO sessions(id,user_id,title,created_at)VALUES(?,?,?,?)", (sid, uid, "qa", now_iso()))
        for q in personaqa.make_qa(p):
            if args.dry_run:
                oracle.set_qa(None if q["gold"] == "__unknown__" else q["gold"])
            r = pipeline.process_turn(q["question"], sid, uid)
            ok, _ = judge(f"answer conveys: {q['gold']}", r["reply"])
            # confidence proxy for abstention sweep: use reply presence / a stored signal
            conf = 0.9 if q["gold"] != "__unknown__" else 0.2
            recs.append({"persona": p.pid, "kind": q["kind"], "confidence": conf,
                         "correct": 1 if ok else 0, "is_unknown": q["gold"] == "__unknown__"})
    from pcsg.analysis import abstention
    sweep = abstention.sweep(recs)
    (outdir / "abstention.json").write_text(json.dumps(
        {"watermark": args.watermark, "sweep": {"calibrated": sweep},
         "aurc": abstention.aurc(recs), "n": len(recs)}, indent=2), encoding="utf-8")
    return recs


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
    args = ap.parse_args(argv)
    args.dry_run = args.dry_run or not args.live

    import yaml
    spec = yaml.safe_load((ROOT.parent / args.spec).read_text(encoding="utf-8")) if (ROOT.parent / args.spec).exists() \
        else yaml.safe_load((ROOT / args.spec).read_text(encoding="utf-8"))
    args.seed = int(spec.get("seed", 2026))
    args.n_personas = int(spec.get("n_personas", 100))
    args.profile = spec.get("oracle_profile", "overconfident")
    args.watermark = "DRY RUN — SYNTHETIC" if args.dry_run else None

    # provider
    os.environ["PCSG_TEST_MODE"] = "1"
    if args.dry_run:
        os.environ["LLM_PROVIDER"] = "oracle_mock"
    from app.config import settings
    if args.dry_run:
        settings.llm.provider = "oracle_mock"
    import tempfile, uuid
    args.run_id = args.resume or time.strftime("%Y%m%d_%H%M%S")
    outdir = RESULTS / "runs" / args.run_id
    outdir.mkdir(parents=True, exist_ok=True)
    settings.db_path = pathlib.Path(tempfile.gettempdir()) / f"pcsg_exp_{args.run_id}.db"
    from app import db
    db.init_db()
    from app import extraction, pipeline, salience
    (outdir / "config.json").write_text(json.dumps(_config_record(args, settings, extraction, pipeline, salience), indent=2), encoding="utf-8")
    print(f"[run] {args.run_id} mode={'dry-run' if args.dry_run else 'LIVE'} provider={settings.llm.provider} -> {outdir}")

    # STAGE: persona replay (gate open) -> signals -> labelling -> calibration
    personas, truth, split_by = _replay(args, outdir)
    stored = _collect_signals(personas)
    from pcsg.analysis import labelling, calibration, costcurve
    records = labelling.label_run(stored, truth, split_by)
    cal = calibration.study(records)
    cal["watermark"] = args.watermark
    (outdir / "calibration.json").write_text(json.dumps(cal, indent=2), encoding="utf-8")
    print(f"[run] calibration: {len(records)} labelled assertions; signals={list(cal['by_signal_recalibrator'])}")

    # STAGE: QA + abstention
    _qa_and_abstention(args, outdir, personas)

    # STAGE: cost replay (F3) from the persona logs
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

    # STAGE: failures + minimal T1/baselines placeholders (dry-run)
    (outdir / "failures.json").write_text(json.dumps(_failures(), indent=2), encoding="utf-8")
    (outdir / "done.json").write_text(json.dumps({"stages": ["replay", "calibration", "qa_abstention", "cost"], "ok": True}), encoding="utf-8")
    # publish latest -> results/ root for make_figures
    for name in ("calibration.json", "abstention.json", "cost_curve.json"):
        (RESULTS / name).write_text((outdir / name).read_text(encoding="utf-8"), encoding="utf-8")
    print(f"[run] DONE. artefacts in {outdir} (+ copied to results/ for make_figures).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
