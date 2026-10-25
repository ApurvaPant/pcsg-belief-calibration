"""Write results/OVERNIGHT_SUMMARY.md from whatever the overnight steps produced.
Defensive: every section degrades to 'not produced' if its inputs are missing.

  python -m pcsg.overnight_summary <model2_run_id> <lme_out> <paraphrase_out> <judge_out>
"""
from __future__ import annotations
import json, pathlib, sys, datetime as dt

REPO = pathlib.Path(__file__).resolve().parents[2]


def _load(p):
    try:
        return json.loads(pathlib.Path(p).read_text(encoding="utf-8"))
    except Exception:
        return None


def _calls(p):
    try:
        return sum(1 for _ in open(p, encoding="utf-8"))
    except Exception:
        return 0


def main(argv=None):
    a = (argv or sys.argv[1:]) + [""] * 4
    model2_run, lme_out, para_out, judge_out = a[0], a[1], a[2], a[3]
    R = REPO / "results"
    L = ["# Overnight run summary", "",
         f"Generated {dt.datetime.now():%Y-%m-%d %H:%M} local.", "",
         "## Model availability incident",
         "`nvidia/nemotron-3-super-120b-a12b` (main study model) was WITHDRAWN from the NVIDIA hosted "
         "API (confirmed removed from build.nvidia.com 2026-10-25 19:49 IST; live 404 "
         "'Function ... Not found for account'). See results/OVERNIGHT_LOG.md for the exact error and the "
         "models checked. ALL new runs below use `openai/gpt-oss-20b` (reasoning_effort=low, write-path) "
         "with `minimaxai/minimax-m3.1-flash` as the LongMemEval judge / paraphraser. The main run "
         "`main_20261015_000553` (Nemotron 3 Super) is untouched and remains the main study.", ""]

    # STEP 4
    m2 = _load(R / "model2" / model2_run / "model2_metrics.json")
    L += ["## STEP 4 — second extraction model (gpt-oss-20b), 12 test personas"]
    if m2:
        g = m2["signals"]["gpt"]; gap = m2["auroc_gap"]["gpt"]
        def au(d): return (d or {}).get("auroc", {}).get("val") if d else None
        L += [f"- assertions: gpt={m2['n_gpt']} vs Nemotron(main)={m2['n_main']}; rule-A acc "
              f"gpt={m2['acc_abc']['gpt']['A']} vs main={m2['acc_abc']['main']['A']}",
              f"- AUROC verbalized={au(g.get('verbalized'))} vs self_consistency={au(g.get('self_consistency'))}",
              f"- **verbalized<self_consistency gap (SC-verb) median={gap['median_gap'] if gap else 'n/a'}, "
              f"95%CI {gap['ci'] if gap else 'n/a'}** (main-run gap: "
              f"{(m2['auroc_gap']['main'] or {}).get('median_gap','n/a')})",
              f"- report: results/model2/{model2_run}/MODEL2.md"]
    else:
        L += ["- not produced (see OVERNIGHT_LOG.md)"]
    L += [f"- write-path model calls logged: {_calls(R/'runs'/model2_run/'llm_cache.jsonl')}", ""]

    # STEP 1
    t5 = _load(pathlib.Path(lme_out) / "t5_metrics.json") if lme_out else None
    L += ["## STEP 1 — LongMemEval (gpt-oss-20b answerers; minimax judge, verbatim prompts)"]
    if t5:
        L += [f"- {t5['n']} questions ({t5['n_abstention']} abstention); judge: {t5['judge_source']}"]
        for s, m in t5["per_system"].items():
            L.append(f"  - {s}: overall {m['overall']} {m['ci']}, coverage {m['coverage']}, "
                     f"selective-acc {m['selective_acc']}, tok/q {m['tokens_per_q']}")
        L += [f"- reports: {lme_out}/T5.md, riskcov_lme.csv, judge_check_lme.csv, STATS.md"]
    else:
        L += [f"- not produced / partial (answers.jsonl may exist at {lme_out})"]
    L += [f"- model calls logged: {_calls(pathlib.Path(lme_out)/'llm_cache.jsonl') if lme_out else 0}", ""]

    # STEP 3
    L += ["## STEP 3 — second-judge validation (ADAPTED: LME answers, Tier-2 not persisted)"]
    jmd = (pathlib.Path(judge_out) / "JUDGE2.md") if judge_out else None
    if jmd and jmd.exists():
        head = [ln for ln in jmd.read_text(encoding="utf-8").splitlines() if "agreement" in ln.lower() and "kappa" in ln.lower()]
        L += [f"- {head[0] if head else 'see JUDGE2.md'}", f"- report: {judge_out}/JUDGE2.md"]
    else:
        L += ["- not produced"]
    L += [""]

    # STEP 2
    L += ["## STEP 2 — paraphrase robustness (minimax paraphraser; gpt-oss k=3)"]
    pmd = (pathlib.Path(para_out) / "PARAPHRASE.md") if para_out else None
    if pmd and pmd.exists():
        L += ["- " + ln for ln in pmd.read_text(encoding="utf-8").splitlines()[5:9] if ln.strip()]
        L += [f"- report: {para_out}/PARAPHRASE.md"]
    else:
        L += ["- not produced / partial"]
    L += [f"- model calls logged: {_calls(pathlib.Path(para_out)/'llm_cache.jsonl') if para_out else 0}", ""]

    (R / "OVERNIGHT_SUMMARY.md").write_text("\n".join(L), encoding="utf-8")
    print("[summary] wrote results/OVERNIGHT_SUMMARY.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
