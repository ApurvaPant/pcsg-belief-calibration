"""STEP 1 overnight LongMemEval runner (2026-10-25; Nemotron 3 Super withdrawn).

All four systems (no_memory / full_context / vector_rag / pcsg) answer with
openai/gpt-oss-20b (self-consistency OFF; reasoning stripped before parsing).
Judge = minimaxai/minimax-m3.1-flash using the OFFICIAL LongMemEval per-type
prompts, copied VERBATIM from the repo's evaluate_qa.py (get_anscheck_prompt):
  repo github.com/xiaowu0162/LongMemEval  src/evaluation/evaluate_qa.py  (MIT)
Only the knowledge-update, single-session-preference and abstention templates are
used (the only types in this study's selection). Temperature 0; yes/no parsed from
the FINAL answer only (llm.py already strips <think>); raw judge output stored; one
parse retry, then "unparsed".

Resumable by question id; respects results/PAUSE and a wall-clock --until cutoff.
Every call is logged to <outdir>/llm_cache.jsonl.

  python -m pcsg.lme_overnight --out results/lme/<run> --select full|75|pilot [--until HH:MM]
Outputs: answers.jsonl, judged.jsonl, T5.md, riskcov_lme.csv, judge_check_lme.csv, STATS.md.
"""
from __future__ import annotations
import argparse, datetime as dt, json, pathlib, sys, time
import numpy as np

# ── VERBATIM official LongMemEval judge templates (evaluate_qa.py, MIT) ─────── #
JUDGE_SOURCE = ("github.com/xiaowu0162/LongMemEval src/evaluation/evaluate_qa.py "
                "(get_anscheck_prompt); MIT; copied verbatim")
T_KU = "I will give you a question, a correct answer, and a response from a model. Please answer yes if the response contains the correct answer. Otherwise, answer no. If the response contains some previous information along with an updated answer, the response should be considered as correct as long as the updated answer is the required answer.\n\nQuestion: {}\n\nCorrect Answer: {}\n\nModel Response: {}\n\nIs the model response correct? Answer yes or no only."
T_SSP = "I will give you a question, a rubric for desired personalized response, and a response from a model. Please answer yes if the response satisfies the desired response. Otherwise, answer no. The model does not need to reflect all the points in the rubric. The response is correct as long as it recalls and utilizes the user's personal information correctly.\n\nQuestion: {}\n\nRubric: {}\n\nModel Response: {}\n\nIs the model response correct? Answer yes or no only."
T_ABS = "I will give you an unanswerable question, an explanation, and a response from a model. Please answer yes if the model correctly identifies the question as unanswerable. The model could say that the information is incomplete, or some other information is given but the asked information is not.\n\nQuestion: {}\n\nExplanation: {}\n\nModel Response: {}\n\nDoes the model correctly identify the question as unanswerable? Answer yes or no only."
LABEL = ("memory+answering: openai/gpt-oss-20b (reasoning_effort low); judge: minimaxai/minimax-m3.1-flash. "
         "Nemotron 3 Super was withdrawn from the hosted API 2026-10-25; gpt-oss-20b is retained for the "
         "structured write path because faster models (nemotron-3.5-lightning 0/5, minimax 3/5) did not "
         "return valid 5/5 JSON frames on this endpoint, while gpt-oss-20b did (5/5).")
SYSTEMS = ["no_memory", "full_context", "vector_rag", "pcsg"]


def judge_prompt(qtype, question, gold_or_rubric, response, abstention):
    if abstention:
        return T_ABS.format(question, gold_or_rubric, response)
    if qtype == "single-session-preference":
        return T_SSP.format(question, gold_or_rubric, response)
    return T_KU.format(question, gold_or_rubric, response)  # knowledge-update (+ default)


def _setup_models():
    """gpt-oss-20b for every pipeline/answer role; minimax as the judge. SC OFF."""
    import os
    os.environ["PCSG_SELF_CONSISTENCY"] = "0"
    from app.config import settings
    for role in ("chat", "classifier", "extractor", "simulator"):
        settings.llm.models[role] = "openai/gpt-oss-20b"
    settings.llm.models["judge_longmemeval"] = "minimaxai/minimax-m3.1-flash"
    settings.llm.request_params = {**settings.llm.request_params,
                                   "openai/gpt-oss-20b": {"reasoning_effort": "low"}}
    settings.llm.timeout_seconds = 90     # per-call timeout (task: 90 s)
    from app import llm as _llm
    _llm._MAX_RETRIES = 3                  # task: max 3 retries (exp backoff + jitter already in llm.py)
    return settings


def _select(questions, mode):
    import random
    ku = [q for q in questions if q["question_type"] == "knowledge-update" and not q["is_abstention"]]
    ssp = [q for q in questions if q["question_type"] == "single-session-preference" and not q["is_abstention"]]
    abst = [q for q in questions if q["is_abstention"]]
    if mode == "pilot":
        return ku[:2] + abst[:1] + ssp[:2]
    if mode == "ordered":
        # All _abs first (balanced partial runs), then KU:SSP interleaved 2:1,
        # KU/SSP each shuffled with seed 2026. Processed in this exact order.
        rng = random.Random(2026); rng.shuffle(ku); rng.shuffle(ssp)
        inter, ki, si = [], 0, 0
        while ki < len(ku) or si < len(ssp):
            for _ in range(2):
                if ki < len(ku):
                    inter.append(ku[ki]); ki += 1
            if si < len(ssp):
                inter.append(ssp[si]); si += 1
        return abst + inter
    if mode == "75":
        rng = random.Random(2026)
        return sorted(abst + rng.sample(ku, min(49, len(ku))) + rng.sample(ssp, min(20, len(ssp))),
                      key=lambda q: q["question_id"])
    return questions  # full 108


def _judge_live(qtype, question, gold, answer, abstention):
    """Return (verdict 0/1/None, raw_text). One retry on parse failure."""
    from app import llm
    model = "minimaxai/minimax-m3.1-flash"
    prompt = judge_prompt(qtype, question, gold, answer, abstention)
    raw = ""
    for _ in range(2):
        r = llm.complete([{"role": "user", "content": prompt}], model=model, temperature=0.0, max_tokens=8)
        raw = (r.get("text") or "").strip()
        low = raw.lower()
        if low.startswith("yes"):
            return 1, raw
        if low.startswith("no"):
            return 0, raw
    return None, raw  # unparsed after retry


def _answer_one(q, dry_run=False):
    """Replay the haystack (user turns -> write path) into a fresh DB, answer with
    all four systems, judge each. Mirrors longmemeval_run._run_one (same template,
    256-tok cap) but with gpt-oss answering + minimax verbatim judge."""
    import pathlib as _pl, tempfile, uuid
    from app import db, pipeline
    from app.db import get_conn, now_iso, set_db_path
    from app.config import settings
    from app.utils import new_id
    from pcsg import baselines, longmemeval_run
    dbfile = _pl.Path(tempfile.gettempdir()) / f"lmeov_{uuid.uuid4().hex[:12]}.db"
    set_db_path(dbfile)
    try:
        db.init_db()
        settings.pipeline.salience_threshold = 0.0
        uid, sid = new_id("user"), new_id("sess")
        with get_conn() as c:
            c.execute("INSERT INTO users(id,label,created_at)VALUES(?,?,?)", (uid, "lme", now_iso()))
            c.execute("INSERT INTO sessions(id,user_id,title,created_at)VALUES(?,?,?,?)", (sid, uid, "lme", now_iso()))
        history = longmemeval_run._history_from_sessions(q["sessions"])
        for t in history:
            if t["role"] == "user":
                pipeline.process_turn(t["text"], sid, uid)
        user_turns = [t for t in history if t["role"] == "user"]
        question = f"{q['question']} (as of {q.get('question_date','')})"
        with get_conn() as c:
            ans = {
                "no_memory": baselines.answer_no_memory(question),
                "full_context": baselines.answer_full_context(question, history),
                "vector_rag": baselines.answer_vector_rag(question, user_turns, k=5),
                "pcsg": baselines.answer_pcsg(question, c, uid),
            }
        rec = {"question_id": q["question_id"], "question_type": q["question_type"],
               "is_abstention": bool(q["is_abstention"]), "gold": q.get("answer"), "systems": {}}
        for s, a in ans.items():
            if q["is_abstention"]:
                verdict = 1 if a["abstained"] else 0
                raw = "(abstention: scored on correct abstention, not judged)"
            else:
                verdict, raw = _judge_live(q["question_type"], q["question"], q.get("answer"),
                                           a["answer"], False)
                if a["abstained"]:
                    verdict = 0  # abstaining on an answerable question is wrong
            rec["systems"][s] = {"answer": a["answer"], "confidence": a["confidence"],
                                 "abstained": int(a["abstained"]),
                                 "correct": None if verdict is None else int(verdict),
                                 "judge_raw": raw,
                                 "tokens": a.get("tokens_in", 0) + a.get("tokens_out", 0)}
        return rec
    finally:
        set_db_path(None)
        for suf in ("", "-wal", "-shm"):
            p = _pl.Path(str(dbfile) + suf)
            if p.exists():
                try: p.unlink()
                except OSError: pass


# ── analysis ────────────────────────────────────────────────────────────────#
def _boot_acc(correct, n=2000, seed=2026):
    c = np.array([x for x in correct if x is not None], float)
    if len(c) == 0:
        return (float("nan"), [float("nan"), float("nan")])
    rng = np.random.default_rng(seed)
    means = [c[rng.integers(0, len(c), len(c))].mean() for _ in range(n)]
    return (float(c.mean()), [float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))])


def _mcnemar(a, b):
    """Exact McNemar on paired per-question correctness (a,b aligned). Returns (b01,b10,p)."""
    from scipy.stats import binomtest
    b01 = sum(1 for x, y in zip(a, b) if x == 0 and y == 1)
    b10 = sum(1 for x, y in zip(a, b) if x == 1 and y == 0)
    nd = b01 + b10
    p = 1.0 if nd == 0 else float(binomtest(min(b01, b10), nd, 0.5).pvalue)
    return b01, b10, round(p, 4)


def write_reports(records, outdir):
    outdir = pathlib.Path(outdir)
    answerable = [r for r in records if not r["is_abstention"]]
    abst = [r for r in records if r["is_abstention"]]
    types = sorted({r["question_type"] for r in records})
    L = [f"# T5 — LongMemEval ({len(records)} questions; {len(abst)} abstention)", "",
         f"**{LABEL}**", "", f"Judge: minimaxai/minimax-m3.1-flash, official per-type prompts verbatim "
         f"({JUDGE_SOURCE}).", "",
         "## Accuracy per system (question-level bootstrap 95% CI)", "",
         "| system | overall [95% CI] | " + " | ".join(types) + " | abstention | coverage | selective-acc | tok/q |",
         "|" + "---|" * (len(types) + 6)]
    per = {}
    for s in SYSTEMS:
        allc = [r["systems"][s]["correct"] for r in records if s in r["systems"]]
        m, ci = _boot_acc(allc)
        bytype = {}
        for t in types:
            tc = [r["systems"][s]["correct"] for r in records if r["question_type"] == t and s in r["systems"]]
            tv = [x for x in tc if x is not None]
            bytype[t] = round(sum(tv) / len(tv), 3) if tv else float("nan")
        av = [r["systems"][s]["correct"] for r in abst if s in r["systems"]]
        abst_acc = round(sum(x for x in av if x is not None) / len(av), 3) if av else None
        # coverage = fraction answered (not abstained) on answerable; selective = acc on answered
        answered = [r for r in answerable if s in r["systems"] and not r["systems"][s]["abstained"]]
        cov = round(len(answered) / len(answerable), 3) if answerable else None
        sel = [r["systems"][s]["correct"] for r in answered]
        selv = [x for x in sel if x is not None]
        sel_acc = round(sum(selv) / len(selv), 3) if selv else None
        toks = [r["systems"][s]["tokens"] for r in records if s in r["systems"]]
        tpq = round(sum(toks) / len(toks), 1) if toks else 0
        per[s] = {"overall": round(m, 3), "ci": [round(ci[0], 3), round(ci[1], 3)], "by_type": bytype,
                  "abstention_acc": abst_acc, "coverage": cov, "selective_acc": sel_acc, "tokens_per_q": tpq}
        L.append(f"| {s} | {per[s]['overall']} {per[s]['ci']} | "
                 + " | ".join(str(bytype[t]) for t in types)
                 + f" | {abst_acc} | {cov} | {sel_acc} | {tpq} |")
    # fabrication on abstention: answered (not abstained) an unanswerable question
    L += ["", "## Fabrication on abstention (_abs) questions", "",
          "| system | fabrication-rate (answered an unanswerable) |", "|---|---|"]
    for s in SYSTEMS:
        fv = [1 - r["systems"][s]["abstained"] for r in abst if s in r["systems"]]
        L.append(f"| {s} | {round(sum(fv)/len(fv),3) if fv else 'n/a'} |")
    # McNemar PCSG vs RAG and PCSG vs full-context (answerable, both non-None aligned)
    L += ["", "## McNemar (exact) on answerable questions", ""]
    for other in ("vector_rag", "full_context"):
        pairs = [(r["systems"]["pcsg"]["correct"], r["systems"][other]["correct"])
                 for r in answerable if "pcsg" in r["systems"] and other in r["systems"]
                 and r["systems"]["pcsg"]["correct"] is not None and r["systems"][other]["correct"] is not None]
        if pairs:
            b01, b10, p = _mcnemar([a for a, _ in pairs], [b for _, b in pairs])
            L.append(f"- PCSG vs {other}: discordant pcsg-only-right={b10}, {other}-only-right={b01}, "
                     f"exact p={p} (n={len(pairs)})")
    (outdir / "T5.md").write_text("\n".join(L), encoding="utf-8")
    # riskcov_lme.csv for PCSG (sort by confidence desc, sweep coverage)
    pc = sorted([r["systems"]["pcsg"] for r in answerable if "pcsg" in r["systems"]
                 and r["systems"]["pcsg"]["correct"] is not None],
                key=lambda a: a["confidence"], reverse=True)
    with open(outdir / "riskcov_lme.csv", "w", encoding="utf-8", newline="") as fh:
        fh.write("coverage,risk,confidence\n")
        corr = 0
        for i, a in enumerate(pc, 1):
            corr += a["correct"]
            fh.write(f"{round(i/len(pc),4)},{round(1-corr/i,4)},{a['confidence']}\n")
    # judge_check_lme.csv: 40 judged items stratified by system, empty human_correct, UTF-8 BOM
    rows = []
    for s in SYSTEMS:
        picks = [r for r in records if s in r["systems"] and not r["is_abstention"]][:10]
        for r in picks:
            a = r["systems"][s]
            rows.append([r["question_id"], r["question_type"], s, r.get("gold", ""),
                         a["answer"], a["correct"], a["judge_raw"], ""])
    with open(outdir / "judge_check_lme.csv", "w", encoding="utf-8-sig", newline="") as fh:
        import csv
        w = csv.writer(fh)
        w.writerow(["question_id", "question_type", "system", "gold", "answer", "judge_verdict", "judge_raw", "human_correct"])
        w.writerows(rows[:40])
    (outdir / "t5_metrics.json").write_text(json.dumps({"label": LABEL, "judge_source": JUDGE_SOURCE,
        "n": len(records), "n_abstention": len(abst), "per_system": per}, indent=2), encoding="utf-8")
    return per


def write_stats(questions, outdir):
    """results/lme/STATS.md: per type, #questions / evidence sessions / user turns / user tokens."""
    outdir = pathlib.Path(outdir)
    from pcsg import longmemeval_run
    rows = {}
    for q in questions:
        t = "abstention" if q["is_abstention"] else q["question_type"]
        hist = longmemeval_run._history_from_sessions(q["sessions"])
        uturns = [h for h in hist if h["role"] == "user"]
        utok = sum(len(h["text"].split()) for h in uturns)
        rows.setdefault(t, []).append((len(q["sessions"]), len(uturns), utok))
    L = ["# LongMemEval selection stats", "", f"**{LABEL}**", "",
         "| type | n | sessions (mean/min/max) | user-turns (mean/min/max) | user-tokens (mean/min/max) |",
         "|---|---|---|---|---|"]
    for t, v in sorted(rows.items()):
        sess = [x[0] for x in v]; tur = [x[1] for x in v]; tok = [x[2] for x in v]
        def mmm(a): return f"{sum(a)/len(a):.1f}/{min(a)}/{max(a)}"
        L.append(f"| {t} | {len(v)} | {mmm(sess)} | {mmm(tur)} | {mmm(tok)} |")
    (outdir / "STATS.md").write_text("\n".join(L), encoding="utf-8")


def _parse_hhmm(s):
    if not s:
        return None
    hh, mm = map(int, s.split(":"))
    now = dt.datetime.now()
    c = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return c + dt.timedelta(days=1) if c < now else c


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--select", default="full", choices=["full", "75", "pilot", "ordered"])
    ap.add_argument("--until", default=None, help="HH:MM: stop STARTING new questions after this")
    ap.add_argument("--hard-until", dest="hard_until", default=None,
                    help="HH:MM: hard stop — stop even between in-flight questions")
    ap.add_argument("--limit", type=int, default=0, help="process only the first N of the ordered todo (0=all; pilot)")
    ap.add_argument("--rate", type=float, default=35)
    ap.add_argument("--workers", type=int, default=4, help="parallel questions (each has its own thread-local DB)")
    args = ap.parse_args(argv)
    outdir = pathlib.Path(args.out); outdir.mkdir(parents=True, exist_ok=True)
    _setup_models()
    from app import llm
    llm.set_rate_limit(args.rate)
    llm.set_cache_log(outdir / "llm_cache.jsonl")
    from pcsg import longmemeval
    data = longmemeval.load()
    chosen = _select(data["questions"], args.select)
    write_stats(chosen, outdir)
    ck = outdir / "checkpoint.json"
    done = set(json.loads(ck.read_text()).get("done", [])) if ck.exists() else set()
    recs_path = outdir / "answers.jsonl"
    records = [json.loads(l) for l in recs_path.read_text(encoding="utf-8").splitlines()] if recs_path.exists() else []
    pause = pathlib.Path(__file__).resolve().parents[2] / "results" / "PAUSE"
    cutoff = _parse_hhmm(args.until)
    hard = _parse_hhmm(args.hard_until)
    todo = [q for q in chosen if q["question_id"] not in done]
    if args.limit and args.limit > 0:
        todo = todo[:args.limit]
    print(f"[lme] select={args.select} total={len(chosen)} done={len(done)} todo={len(todo)} "
          f"workers={args.workers} until={args.until or '-'} hard={args.hard_until or '-'}")
    import threading, concurrent.futures as cf
    lock = threading.Lock()
    t0 = time.time(); counter = {"n": 0}
    retries0 = len(llm.RETRY_LOG)

    def _periodic_report():
        try:
            good = [r for r in records if r.get("systems") and not r.get("failed")]
            if len(good) >= 10:
                write_reports(good, outdir)
                (outdir / "judged.jsonl").write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
        except Exception as e:  # noqa: BLE001 — a partial report must never kill the run
            print(f"[lme] periodic T5 skipped: {type(e).__name__}: {str(e)[:60]}")

    prog = outdir / "run_progress.log"

    def _process(q):
        if pause.exists() or (cutoff and dt.datetime.now() >= cutoff) or (hard and dt.datetime.now() >= hard):
            return "skip"
        q_t0 = time.time()
        calls_before = int(llm.cost_summary().get("calls", 0))
        try:
            rec = _answer_one(q)
            status = "ok"
        except Exception as e:  # noqa: BLE001 — one bad question never stops the run
            status = f"FAILED {type(e).__name__}: {str(e)[:60]}"
            rec = {"question_id": q["question_id"], "question_type": q["question_type"],
                   "is_abstention": bool(q["is_abstention"]), "failed": True, "systems": {}}
        secs = time.time() - q_t0
        with lock:
            records.append(rec); done.add(q["question_id"]); counter["n"] += 1
            with open(recs_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec) + "\n")
            ck.write_text(json.dumps({"done": sorted(done)}), encoding="utf-8")
            n = counter["n"]
            cum_calls = int(llm.cost_summary().get("calls", 0))
            # calls_delta brackets THIS question + whatever else finished meanwhile (4-way
            # parallel), so it's an upper bound; cumulative rate is the honest speed signal.
            el = (time.time() - t0) / 60.0
            line = (f"[lme] DONE {q['question_id']} ({q['question_type']}{'/abs' if q['is_abstention'] else ''}) "
                    f"{secs:.0f}s {status} | {n}/{len(todo)} done | ~{cum_calls-calls_before} calls bracket | "
                    f"cum {cum_calls} calls, {cum_calls/el:.1f} calls/min")
            print(line, flush=True)
            with open(prog, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
            if n % 10 == 0:
                print(f"[lme] {n} done; regenerating partial T5.md", flush=True)
                _periodic_report()
        return "done"

    with cf.ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        for f in cf.as_completed([ex.submit(_process, q) for q in todo]):
            f.result()
    n_done = counter["n"]; elapsed = time.time() - t0
    calls = int(llm.cost_summary().get("calls", 0))
    retries = len(llm.RETRY_LOG) - retries0
    metrics = {"n_done": n_done, "elapsed_min": round(elapsed / 60, 2), "calls": calls, "retries": retries,
               "retry_rate": round(retries / calls, 4) if calls else 0.0,
               "calls_per_q": round(calls / n_done, 1) if n_done else 0,
               "min_per_q": round(elapsed / 60 / n_done, 2) if n_done else 0,
               "calls_per_min": round(calls / (elapsed / 60), 1) if elapsed else 0,
               "workers": args.workers, "spent_tokens": llm.spent_tokens()}
    (outdir / "run_metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    if n_done:
        print(f"[lme] processed {n_done} in {elapsed/60:.1f} min | calls={calls} retries={retries} "
              f"retry_rate={metrics['retry_rate']} | {metrics['calls_per_q']} calls/q, "
              f"{metrics['min_per_q']} min/q, {metrics['calls_per_min']} calls/min")
    (outdir / "judged.jsonl").write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
    good = [r for r in records if r.get("systems") and not r.get("failed")]
    if len(good) >= 10:
        write_reports(good, outdir)
    print(f"[lme] done: {len(good)} judged records -> {outdir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
