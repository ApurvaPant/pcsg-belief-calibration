"""LongMemEval runner (STEP 3 / table T5, Tier 3).

For each selected question: a FRESH SQLite DB; replay the haystack sessions in
chronological order using each session's date as message time (user turns go
through the full PCSG pipeline; assistant turns are injected as context, as in
the persona runner); then ask the question with its question_date. All FOUR
systems from STEP 2 answer each question; answers are graded by the per-type
judge (judge_longmemeval role, official per-type prompts). Resumable by question
id; questions run with a separate DB each (parallel on live, serialised under the
deterministic oracle); the global rate limiter is used for live calls.

Metrics (per system): accuracy per question type + overall, abstention accuracy
on _abs questions, and answer-level ECE from each system's answer confidence.
Output -> results/.../longmemeval.json (T5).
"""
from __future__ import annotations

import json
import pathlib
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor

from pcsg import baselines
from pcsg.analysis import calibration

# Official per-type judge prompts live in the LongMemEval repo's evaluation
# script: `longmemeval/src/evaluation/evaluate_qa.py` (function `get_anscheck_prompt`).
# For a LIVE run paste those verbatim here, keyed by question type, and grade with
# the judge_longmemeval model. In dry-run the deterministic oracle judge is used,
# so no external prompt text is reproduced in this offline session.
JUDGE_SOURCE = "longmemeval/src/evaluation/evaluate_qa.py :: get_anscheck_prompt"


def _history_from_sessions(sessions: list[dict]) -> list[dict]:
    """Flatten chronological sessions into one history list with turn indices."""
    history, i = [], 0
    for s in sessions:
        for t in s.get("turns", []):
            history.append({"role": t.get("role", "user"), "text": t.get("content", ""),
                            "turn": i, "date": s.get("date")})
            i += 1
    return history


def _oracle_judge(gold, answer: str) -> int:
    from pcsg import oracle
    ok = "PASS" in oracle._judge(f"RUBRIC:answer conveys {gold} REPLY:{answer}")
    return 1 if ok else 0


def _run_one(q: dict, dry_run: bool) -> dict:
    """Replay one question in its OWN DB and answer with all four systems."""
    from app import db, pipeline
    from app.db import get_conn, now_iso, set_db_path
    from app.config import settings
    from app.utils import new_id
    from pcsg import oracle

    dbfile = pathlib.Path(tempfile.gettempdir()) / f"lme_{uuid.uuid4().hex[:12]}.db"
    set_db_path(dbfile)
    try:
        db.init_db()
        settings.pipeline.salience_threshold = 0.0
        uid, sid = new_id("user"), new_id("sess")
        with get_conn() as c:
            c.execute("INSERT INTO users(id,label,created_at)VALUES(?,?,?)", (uid, "lme", now_iso()))
            c.execute("INSERT INTO sessions(id,user_id,title,created_at)VALUES(?,?,?,?)", (sid, uid, "lme", now_iso()))
        history = _history_from_sessions(q["sessions"])
        # replay: user turns -> pipeline (builds the graph); assistant turns are context only.
        for t in history:
            if t["role"] == "user":
                if dry_run:
                    oracle.set_turn([])      # haystack turns are free text; no scripted assertions
                pipeline.process_turn(t["text"], sid, uid)
        user_turns = [t for t in history if t["role"] == "user"]
        gold = None if q["is_abstention"] else q["answer"]
        if dry_run:
            oracle.set_qa(None if q["is_abstention"] else gold)
        question = f"{q['question']} (as of {q.get('question_date','')})"
        with get_conn() as c:
            answers = {
                "no_memory": baselines.answer_no_memory(question),
                "full_context": baselines.answer_full_context(question, history),
                "vector_rag": baselines.answer_vector_rag(question, user_turns, k=5),
                "pcsg": baselines.answer_pcsg(question, c, uid),
            }
        out = {"question_id": q["question_id"], "question_type": q["question_type"],
               "is_abstention": bool(q["is_abstention"]), "systems": {}}
        for s, a in answers.items():
            if q["is_abstention"]:
                correct = 1 if a["abstained"] else 0
            else:
                correct = _oracle_judge(gold, a["answer"]) if dry_run else _live_judge(q, a["answer"])
                correct = correct if not a["abstained"] else 0
            out["systems"][s] = {"answer": a["answer"], "confidence": a["confidence"],
                                 "abstained": int(a["abstained"]), "correct": int(correct),
                                 "tokens": a.get("tokens_in", 0) + a.get("tokens_out", 0)}
        return out
    finally:
        set_db_path(None)
        for suf in ("", "-wal", "-shm"):
            p = pathlib.Path(str(dbfile) + suf)
            if p.exists():
                try:
                    p.unlink()
                except OSError:
                    pass


def _live_judge(q: dict, answer: str) -> int:  # pragma: no cover - live only
    """LIVE grading with the judge_longmemeval model + official per-type prompt.
    Paste the verbatim prompt from JUDGE_SOURCE before the live run."""
    from app import llm
    model = llm.resolve_model("judge_longmemeval") or llm.resolve_model("judge")
    prompt = (f"[[TASK:LME_JUDGE:{q['question_type']}]]\n"
              f"Question: {q['question']}\nGold answer: {q['answer']}\n"
              f"Candidate answer: {answer}\nReply PASS if the candidate is correct, else FAIL.")
    r = llm.complete([{"role": "user", "content": prompt}], model=model, max_tokens=4)
    return 1 if "PASS" in (r.get("text") or "").upper() else 0


def _metrics(records: list[dict]) -> dict:
    systems = baselines.SYSTEMS
    out = {s: {} for s in systems}
    for s in systems:
        rs = [r for r in records if s in r["systems"]]
        n = len(rs) or 1
        by_type: dict[str, list] = {}
        for r in rs:
            by_type.setdefault(r["question_type"], []).append(r["systems"][s]["correct"])
        overall = sum(r["systems"][s]["correct"] for r in rs) / n
        abst = [r for r in rs if r["is_abstention"]]
        abst_acc = (sum(r["systems"][s]["correct"] for r in abst) / len(abst)) if abst else None
        answerable = [r for r in rs if not r["is_abstention"]]
        ece = (calibration.ece([r["systems"][s]["confidence"] for r in answerable],
                               [r["systems"][s]["correct"] for r in answerable], 10, "uniform")
               if answerable else None)
        out[s] = {
            "n": len(rs),
            "accuracy_overall": round(overall, 4),
            "accuracy_by_type": {t: round(sum(v) / len(v), 4) for t, v in by_type.items()},
            "abstention_accuracy": round(abst_acc, 4) if abst_acc is not None else None,
            "answer_ece": round(ece, 4) if ece is not None else None,
            "tokens_per_q": round(sum(r["systems"][s]["tokens"] for r in rs) / n, 1),
        }
    return out


def run(questions: list[dict], outdir: pathlib.Path, *, dry_run: bool = True,
        workers: int = 1, rate_limit_per_min=None, watermark=None, resume: bool = True) -> dict:
    """Run T5 over `questions`. Resumable by question id (checkpoint of done ids).
    Serialised under the deterministic oracle (shared singleton); parallel on live."""
    from app import llm
    outdir.mkdir(parents=True, exist_ok=True)
    if rate_limit_per_min:
        llm.set_rate_limit(rate_limit_per_min)
    ckpt = outdir / "lme_checkpoint.json"
    recs_path = outdir / "lme_records.jsonl"
    done = set(json.loads(ckpt.read_text()).get("done", [])) if (resume and ckpt.exists()) else set()
    records = [json.loads(l) for l in recs_path.read_text(encoding="utf-8").splitlines()] \
        if (resume and recs_path.exists()) else []
    todo = [q for q in questions if q["question_id"] not in done]
    eff_workers = 1 if dry_run else max(1, workers)   # oracle is a shared singleton

    def _store(rec):
        records.append(rec)
        done.add(rec["question_id"])
        with open(recs_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
        ckpt.write_text(json.dumps({"done": sorted(done)}), encoding="utf-8")

    if eff_workers == 1:
        for q in todo:
            _store(_run_one(q, dry_run))
    else:  # pragma: no cover - live parallel path
        with ThreadPoolExecutor(max_workers=eff_workers) as ex:
            for rec in ex.map(lambda q: _run_one(q, dry_run), todo):
                _store(rec)

    by_type = {}
    for q in questions:
        by_type[q["question_type"]] = by_type.get(q["question_type"], 0) + 1
    result = {"watermark": watermark, "revision": None, "judge_source": JUDGE_SOURCE,
              "n_questions": len(records), "count_by_type": by_type,
              "n_abstention": sum(1 for q in questions if q["is_abstention"]),
              "metrics": _metrics(records)}
    (outdir / "longmemeval.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result
