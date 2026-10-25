"""STEP 3 overnight second-judge validation (2026-10-25).

ADAPTED SCOPE (recorded): the original plan re-judged 150 stratified Tier-2
main-run answers, but the main run (read-only) did NOT persist per-item Tier-2
answer text or verdicts (only aggregates), so they cannot be re-judged. Instead
this validates the LongMemEval judge on tonight's STEP-1 answers, which DO store
per-item answer text + the primary (minimax) verdict: a SECOND judge,
openai/gpt-oss-20b, re-judges the same answers with the SAME official verbatim
prompts, and we report raw agreement + Cohen's kappa vs the primary minimax judge,
stratified by system and question type.

  python -m pcsg.judge2_overnight --lme-run results/lme/<run> --out results/judge_validation --n 150
"""
from __future__ import annotations
import argparse, json, pathlib, random, sys
from sklearn.metrics import cohen_kappa_score

SECOND_JUDGE = "openai/gpt-oss-20b"
PRIMARY_JUDGE = "minimaxai/minimax-m3.1-flash"
NOTE = ("ADAPTED: Tier-2 main-run per-item answers were not persisted (read-only run), so the second "
        "judge (gpt-oss-20b) was validated against the primary judge (minimax-m3.1-flash) on tonight's "
        "LongMemEval answers instead. Nemotron 3 Super withdrawn 2026-10-25.")


def _second_verdict(qtype, question, gold, answer):
    from app import llm
    from pcsg.lme_overnight import judge_prompt
    prompt = judge_prompt(qtype, question, gold, answer, abstention=False)
    for _ in range(2):
        r = llm.complete([{"role": "user", "content": prompt}], model=SECOND_JUDGE, temperature=0.0, max_tokens=8)
        low = (r.get("text") or "").strip().lower()
        if low.startswith("yes"): return 1
        if low.startswith("no"): return 0
    return None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--lme-run", required=True)
    ap.add_argument("--out", default="results/judge_validation")
    ap.add_argument("--n", type=int, default=150)
    ap.add_argument("--rate", type=float, default=35)
    args = ap.parse_args(argv)
    REPO = pathlib.Path(__file__).resolve().parents[2]
    outdir = (REPO / args.out) if not pathlib.Path(args.out).is_absolute() else pathlib.Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    from app.config import settings
    settings.llm.request_params = {**settings.llm.request_params, SECOND_JUDGE: {"reasoning_effort": "low"}}
    from app import llm
    llm.set_rate_limit(args.rate); llm.set_cache_log(outdir / "llm_cache.jsonl")

    jp = pathlib.Path(args.lme_run) / "judged.jsonl"
    if not jp.exists():
        print(f"[judge2] no LME judged.jsonl at {jp} — STEP 1 produced none; skipping (logged).")
        (outdir / "JUDGE2.md").write_text(
            f"# JUDGE2 — second judge validation (STEP 3)\n\n**SKIPPED:** {NOTE}\n\n"
            f"No STEP-1 LongMemEval answers were available to cross-judge.\n", encoding="utf-8")
        return 0
    recs = [json.loads(l) for l in jp.read_text(encoding="utf-8").splitlines()]
    # candidate items: answerable, with a primary verdict + answer text
    items = []
    for r in recs:
        if r.get("is_abstention") or r.get("failed"):
            continue
        for s, a in r.get("systems", {}).items():
            if a.get("correct") is not None and a.get("answer"):
                items.append({"qid": r["question_id"], "qtype": r["question_type"], "system": s,
                              "question": None, "gold": r.get("gold"), "answer": a["answer"],
                              "primary": int(a["correct"])})
    # stratify by system, sample up to n
    random.seed(2026)
    bysys = {}
    for it in items: bysys.setdefault(it["system"], []).append(it)
    per = max(1, args.n // max(len(bysys), 1))
    chosen = []
    for s, lst in bysys.items():
        random.shuffle(lst); chosen += lst[:per]
    chosen = chosen[:args.n]
    # need the question text: reload from dataset by qid
    from pcsg import longmemeval
    qtext = {q["question_id"]: q["question"] for q in longmemeval.load()["questions"]}
    rows, p1, p2 = [], [], []
    for it in chosen:
        q = qtext.get(it["qid"], it["qid"])
        v2 = _second_verdict(it["qtype"], q, it["gold"], it["answer"])
        if v2 is None:
            continue
        rows.append({**it, "question": q, "second": v2})
        p1.append(it["primary"]); p2.append(v2)
    agree = round(sum(1 for a, b in zip(p1, p2) if a == b) / len(p1), 4) if p1 else None
    kappa = round(float(cohen_kappa_score(p1, p2)), 4) if len(set(p1)) > 1 and len(set(p2)) > 1 else None
    # per-system + per-type breakdown
    def _break(key):
        out = {}
        for r in rows:
            k = r[key]; out.setdefault(k, [[], []])
            out[k][0].append(r["primary"]); out[k][1].append(r["second"])
        res = {}
        for k, (x, y) in out.items():
            ag = round(sum(1 for a, b in zip(x, y) if a == b) / len(x), 4) if x else None
            kp = round(float(cohen_kappa_score(x, y)), 4) if len(set(x)) > 1 and len(set(y)) > 1 else None
            res[k] = {"n": len(x), "agreement": ag, "kappa": kp}
        return res
    L = ["# JUDGE2 — second judge validation (STEP 3)", "", f"_{NOTE}_", "",
         f"Primary judge: {PRIMARY_JUDGE} | Second judge: {SECOND_JUDGE} (reasoning_effort low)", "",
         f"Items cross-judged: {len(rows)}", "",
         f"**Overall raw agreement: {agree} | Cohen's kappa: {kappa}**", "",
         "## By system", "", "| system | n | agreement | kappa |", "|---|---|---|---|"]
    for k, v in _break("system").items():
        L.append(f"| {k} | {v['n']} | {v['agreement']} | {v['kappa']} |")
    L += ["", "## By question type", "", "| type | n | agreement | kappa |", "|---|---|---|---|"]
    for k, v in _break("qtype").items():
        L.append(f"| {k} | {v['n']} | {v['agreement']} | {v['kappa']} |")
    (outdir / "JUDGE2.md").write_text("\n".join(L), encoding="utf-8")
    with open(outdir / "judge2_items.jsonl", "w", encoding="utf-8") as fh:
        for r in rows: fh.write(json.dumps(r) + "\n")
    print(f"[judge2] {len(rows)} items; agreement={agree} kappa={kappa} -> {outdir/'JUDGE2.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
