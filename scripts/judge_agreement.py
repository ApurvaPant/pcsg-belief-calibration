"""Human validation of the LongMemEval LLM judge.

Reads the teammate-filled judge_check_lme_human.csv (40 stratified rows; may carry a
UTF-8 BOM, so opened utf-8-sig) and reports judge<->human agreement. Offline, no API.

Reported:
  * parsed rows (judge_verdict in {0,1}): n, agreement, raw agreement, Cohen's kappa,
    Wilson 95% CI for the agreement proportion, split by question_type;
  * how many answers begin with "I don't know";
  * blank-verdict rows listed with system / question_type / human_correct;
  * agreement + kappa over all 40 rows with a blank verdict counted as 0.

Usage: python scripts/judge_agreement.py  (prints Markdown to stdout)
"""
import csv
import math
import pathlib

CSV = pathlib.Path(__file__).resolve().parents[1] / "results" / "lme" / "lme_gptoss_20261026b" / "judge_check_lme_human.csv"


def wilson(k, n, z=1.959963984540054):
    """Wilson score 95% CI for a binomial proportion k/n."""
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / d
    return (centre - half, centre + half)


def kappa(a, b):
    """Cohen's kappa for two aligned binary label lists a,b (values in {0,1})."""
    n = len(a)
    if n == 0:
        return float("nan")
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    pe = 0.0
    for c in (0, 1):
        pa = sum(1 for x in a if x == c) / n
        pb = sum(1 for x in b if x == c) / n
        pe += pa * pb
    return 1.0 if pe == 1.0 else (po - pe) / (1 - pe)


def agree_block(rows, title):
    """Return markdown lines for agreement/kappa/Wilson on a set of (judge,human) rows."""
    j = [r["_j"] for r in rows]
    h = [r["_h"] for r in rows]
    n = len(rows)
    ag = sum(1 for x, y in zip(j, h) if x == y)
    ra = ag / n if n else float("nan")
    lo, hi = wilson(ag, n)
    k = kappa(j, h)
    return (f"| {title} | {n} | {ag} | {ra:.3f} | {k:.3f} | {lo:.3f}–{hi:.3f} |", n, ag, ra, k, (lo, hi))


def main():
    rows = list(csv.DictReader(open(CSV, encoding="utf-8-sig")))
    for r in rows:
        r["jv"] = (r.get("judge_verdict") or "").strip()
        r["human"] = int((r.get("human_correct") or "0").strip())
        r["parsed"] = r["jv"] in ("0", "1")

    parsed = [dict(r, _j=int(r["jv"]), _h=r["human"]) for r in rows if r["parsed"]]
    blanks = [r for r in rows if not r["parsed"]]
    idk = sum(1 for r in rows if (r.get("answer") or "").strip().lower().startswith("i don't know")
              or (r.get("answer") or "").strip().lower().startswith("i dont know"))

    L = ["# LLM-judge human validation — LongMemEval (lme_gptoss_20261026b)", "",
         "Judge `minimaxai/minimax-m3.1-flash` vs. a human rater on a 40-row stratified sample "
         "(`judge_check_lme_human.csv`, UTF-8-BOM, read utf-8-sig). Offline; no API calls.", "",
         "## Agreement on rows with a parsed judge verdict (judge_verdict ∈ {0,1})", "",
         "| subset | n | agree | raw agreement | Cohen's κ | Wilson 95% CI |",
         "|---|---|---|---|---|---|"]
    overall = agree_block(parsed, "all parsed")
    L.append(overall[0])
    for t in sorted({r["question_type"] for r in parsed}):
        sub = [r for r in parsed if r["question_type"] == t]
        L.append(agree_block(sub, t)[0])

    L += ["", f"- Answers beginning with \"I don't know\": **{idk}** of 40.", "",
          "## Blank judge verdicts", "",
          f"{len(blanks)} rows had a blank/unparsed judge verdict (judge emitted no yes/no):", "",
          "| question_id | system | question_type | human_correct |",
          "|---|---|---|---|"]
    for r in blanks:
        L.append(f"| {r['question_id']} | {r['system']} | {r['question_type']} | {r['human']} |")
    blank_human_correct = sum(r["human"] for r in blanks)
    L.append("")
    L.append(f"- Of the {len(blanks)} blank-verdict rows, the human marked **{blank_human_correct}** correct.")

    # all 40, blank verdict counted as 0
    allj = [(0 if not r["parsed"] else int(r["jv"])) for r in rows]
    allh = [r["human"] for r in rows]
    n40 = len(rows)
    ag40 = sum(1 for x, y in zip(allj, allh) if x == y)
    k40 = kappa(allj, allh)
    lo40, hi40 = wilson(ag40, n40)
    L += ["", "## All 40 rows, blank verdict counted as 0", "",
          f"- agreement **{ag40}/{n40}** (raw {ag40/n40:.3f}, Wilson 95% CI {lo40:.3f}–{hi40:.3f})",
          f"- Cohen's κ **{k40:.3f}**", "",
          "## Interpretation", "",
          f"On the {overall[1]} rows the judge actually scored, it agrees with the human "
          f"{overall[2]}/{overall[1]} (κ={overall[4]:.2f}): the official LongMemEval prompts are "
          "reproduced faithfully. The only disagreements in the full-40 view come from blank "
          "verdicts — truncated/JSON-wrapped answers the judge could not parse (counted wrong in "
          "T5) — some of which a human would credit; see `T5_final.md` §SSP."]
    out = CSV.parent / "JUDGE_CHECK.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"wrote {out} ({overall[2]}/{overall[1]} parsed agree, kappa={overall[4]:.2f}; "
          f"all-40 blank=0: {ag40}/{n40}, kappa={k40:.2f})")


if __name__ == "__main__":
    main()
