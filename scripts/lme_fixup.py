"""LME fix-up (2026-10-26). Runs AFTER the lme_gptoss_20261026b run has exited.

 1. Re-run questions that FAILED (HTTP 429 / other errors) with 3 workers, a >=60s
    floor backoff on retryable errors (429/5xx), gentler 20 RPM, stop starting new
    at 09:10 local (IST).
 2. Unwrap answers that leaked a {"answer": ...} JSON wrapper (truncated/malformed
    JSON that baselines._parse could not load), recompute abstention on the clean text.
 3. Re-judge ONLY records whose verdict is blank/unparsed (correct is None), with the
    same judge model + official verbatim prompts.
 4. Regenerate T5_final.md (per-system & per-type acc + CIs, fabrication, coverage,
    selective acc, McNemar) with a fix-up summary (re-run / re-judged / still-failed /
    unwrapped counts); regenerate judge_check_lme.csv (40 stratified, empty
    human_correct, UTF-8 BOM) from the FINAL records.

Run from backend/ with PYTHONPATH=. (the .ps1 does this). Real API (gpt-oss + minimax).
"""
import sys, os, json, time, re, threading, datetime as dt, pathlib
import concurrent.futures as cf

sys.path.insert(0, ".")
os.environ.pop("LLM_PROVIDER", None)      # real API, not mock
os.environ.pop("PCSG_OFFLINE", None)

REPO = pathlib.Path(__file__).resolve().parents[1]
OUT = REPO / "results" / "lme" / "lme_gptoss_20261026b"
LOG = REPO / "results" / "OVERNIGHT_LOG.md"
STOP = dt.datetime.now().replace(hour=9, minute=10, second=0, microsecond=0)  # 09:10 IST (local)


def log(m):
    line = f"- {dt.datetime.now():%Y-%m-%d %H:%M:%S} [LME-FIXUP] {m}"
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    print(line, flush=True)


def main():
    from app import llm
    from pcsg import lme_overnight as L, longmemeval, baselines

    L._setup_models()
    llm.set_rate_limit(20)                 # gentler than the 35 the main run used
    llm.set_cache_log(OUT / "llm_cache.jsonl")
    # >=60s floor backoff on 429/5xx, and ride through more retries than the default.
    llm._MAX_RETRIES = 8
    _orig_backoff = llm._backoff_seconds
    llm._backoff_seconds = lambda attempt: max(60.0, _orig_backoff(attempt))

    data = longmemeval.load()
    byid = {q["question_id"]: q for q in data["questions"]}

    # ---- load current records, keep the best per qid (prefer a non-failed one) ----
    raw = [json.loads(l) for l in (OUT / "answers.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    best = {}
    for r in raw:
        qid = r["question_id"]
        if qid not in best or (best[qid].get("failed") and not r.get("failed")):
            best[qid] = r
    records = list(best.values())
    failed = [r for r in records if r.get("failed")]
    log(f"loaded {len(records)} unique records; {len(failed)} failed to re-run; stop-new at {STOP:%H:%M}")

    # ---- 1. re-run failed questions (3 workers) ----
    todo = [byid[r["question_id"]] for r in failed if r["question_id"] in byid]
    new = {}
    lock = threading.Lock()

    def run_one(q):
        if dt.datetime.now() >= STOP:
            return
        try:
            rec = L._answer_one(q)            # internal llm retries now floor at 60s
            with lock:
                new[q["question_id"]] = rec
            log(f"re-ran {q['question_id']} OK")
        except Exception as e:                # noqa: BLE001
            with lock:
                new[q["question_id"]] = {"question_id": q["question_id"], "question_type": q["question_type"],
                                         "is_abstention": bool(q["is_abstention"]), "failed": True, "systems": {}}
            log(f"re-ran {q['question_id']} STILL FAILED {type(e).__name__}: {str(e)[:60]}")

    if todo:
        with cf.ThreadPoolExecutor(max_workers=3) as ex:
            list(ex.map(run_one, todo))
    # merge re-runs back in
    merged = {r["question_id"]: r for r in records}
    merged.update(new)
    records = list(merged.values())
    reran_ok = sum(1 for qid in new if not new[qid].get("failed"))
    still_failed = [r for r in records if r.get("failed")]

    # ---- 2. unwrap leaked {"answer": ...} wrappers + recompute abstention ----
    def unwrap(ans):
        s = ans or ""
        if '"answer"' in s or s.lstrip().startswith("{"):
            try:
                obj = json.loads(re.search(r"\{.*\}", s, re.S).group(0))
                v = str(obj.get("answer", "")).strip()
                if v:
                    return v
            except Exception:  # noqa: BLE001 — truncated/malformed JSON: leave as-is
                pass
        return s

    unwrapped = 0
    for r in records:
        if r.get("failed"):
            continue
        for s, a in r["systems"].items():
            clean = unwrap(a.get("answer"))
            if clean != a.get("answer"):
                a["answer"] = clean
                unwrapped += 1
                if not r["is_abstention"]:
                    a["abstained"] = int(baselines._is_idk(clean))

    # ---- 3. re-judge ONLY blank/unparsed verdicts (correct is None), non-abstention ----
    rejudged = 0
    for r in records:
        if r.get("failed") or r["is_abstention"] or r["question_id"] not in byid:
            continue
        q = byid[r["question_id"]]
        for s, a in r["systems"].items():
            if a.get("abstained"):
                a["correct"] = 0
                continue
            if a.get("correct") is None:
                verdict, raw_v = L._judge_live(q["question_type"], q["question"], r.get("gold"),
                                               a.get("answer"), False)
                a["correct"] = None if verdict is None else int(verdict)
                a["judge_raw"] = raw_v
                rejudged += 1
    log(f"re-ran-ok={reran_ok} still-failed={len(still_failed)} unwrapped={unwrapped} re-judged={rejudged}")

    # ---- 4. persist + regenerate reports ----
    (OUT / "answers_final.jsonl").write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
    (OUT / "judged.jsonl").write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
    good = [r for r in records if r.get("systems") and not r.get("failed")]
    L.write_reports(good, OUT)             # rewrites T5.md, riskcov_lme.csv, judge_check_lme.csv (BOM), t5_metrics.json

    t5 = (OUT / "T5.md").read_text(encoding="utf-8")
    summary = (
        "\n\n## Fix-up pass (2026-10-26, after main run exit)\n\n"
        f"- questions re-run (had been HTTP errors): {len(todo)} attempted, {reran_ok} now OK, "
        f"{len(still_failed)} still failed\n"
        f"- answers unwrapped from leaked `{{\"answer\": ...}}` JSON: {unwrapped}\n"
        f"- blank/unparsed verdicts re-judged: {rejudged}\n"
        f"- final judged records: {len(good)} (of {len(records)} unique questions)\n"
    )
    if still_failed:
        summary += "- still-failed ids: " + ", ".join(sorted(r["question_id"] for r in still_failed)) + "\n"
    (OUT / "T5_final.md").write_text(t5 + summary, encoding="utf-8")
    log(f"wrote T5_final.md ({len(good)} judged); judge_check_lme.csv regenerated. DONE.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        log(f"FATAL {type(e).__name__}: {str(e)[:150]}")
        raise
