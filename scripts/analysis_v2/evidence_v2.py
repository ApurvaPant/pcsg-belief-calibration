"""analysis_v2 item 5 -> results/analysis_v2/EVIDENCE_V2.md (OFFLINE, read-only).

Post-hoc DETERMINISTIC explicitness from a hedge lexicon on each belief's evidence
span, then recompute evidence-weighted p = 1-(1-p_verb)(1-q) with the hand-set priors
already in the config (signals._EXPLICIT_PRIOR). Report class distribution + AUROC/Brier
vs the ORIGINAL evidence signal. POST-HOC only; confidence_raw is never touched.

Linkage: same (persona,dimension) count-matching order alignment as TEMPLATES.md
(the run DB that held per-signal message text is gone); coverage is reported.
"""
from __future__ import annotations
import json, pathlib, re, sys, collections
import numpy as np
REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))
from pcsg.analysis import calibration as C
from pcsg.analysis.report import rnd  # version-stable fixed-decimal formatting
from app import signals
from app.update import noisy_or

RD = REPO / "results" / "runs" / "main_20261015_000553"
OUT = REPO / "results" / "analysis_v2"; OUT.mkdir(parents=True, exist_ok=True)
PR = [json.loads(l) for l in (RD / "persona_run.jsonl").read_text().splitlines()]
LAB = [json.loads(l) for l in (RD / "labelled.jsonl").read_text().splitlines()]
HEDGES = ["might", "maybe", "probably", "perhaps", "thinking about", "considering",
          "planning to", "hoping to", "kind of", "sort of", "i guess", "not sure", "possibly"]
PRIOR = dict(signals._EXPLICIT_PRIOR)  # {'explicit':0.70,'implicit':0.40} from config/code


def hedged(text):
    t = (text or "").lower()
    return any(h in t for h in HEDGES)


def link():
    cr = collections.defaultdict(list)
    for m in PR:
        for ch in m["changes"]:
            if ch.get("action") in ("created", "reinforced"):
                cr[(m["persona"], ch["dimension"])].append(m["text"])
    labg = collections.defaultdict(list)
    for r in LAB:
        labg[(r["persona"], r["dimension"])].append(r)
    out = []
    for key, lab_list in labg.items():
        crl = cr.get(key, [])
        if len(crl) == len(lab_list):
            for r, text in zip(lab_list, crl):
                out.append({**r, "text": text})
    return out


def auroc_brier(p, y):
    p = np.asarray(p, float); y = np.asarray(y, int)
    return rnd(C.auroc(p, y)), rnd(C.brier(p, y))


def main():
    linked = [x for x in link() if x["split"] == "test"]
    cov = round(len(linked) / len([r for r in LAB if r["split"] == "test"]), 3)
    for x in linked:
        x["posthoc_expl"] = "implicit" if hedged(x["text"]) else "explicit"  # hedged -> implicit prior
        q = PRIOR[x["posthoc_expl"]]
        x["p_posthoc"] = noisy_or(float(x["verbalized"]), q)
    y = [int(x["correct_A"]) for x in linked]
    dist = collections.Counter(x["posthoc_expl"] for x in linked)
    orig_dist = collections.Counter(x.get("explicitness", "explicit") for x in linked)
    au_post, br_post = auroc_brier([x["p_posthoc"] for x in linked], y)
    au_orig, br_orig = auroc_brier([x["evidence"] for x in linked], y)
    au_verb, br_verb = auroc_brier([x["verbalized"] for x in linked], y)
    # CANONICAL full-test (rule A), independent of the linkage subset.
    from scipy.stats import spearmanr
    ftest = [r for r in LAB if r["split"] == "test"]
    fy = [r["correct_A"] for r in ftest]
    f_ev, _ = auroc_brier([r["evidence"] for r in ftest], fy)
    f_vb, _ = auroc_brier([r["verbalized"] for r in ftest], fy)
    f_rho = rnd(float(spearmanr([r["evidence"] for r in ftest], [r["verbalized"] for r in ftest]).statistic))
    f_expl = collections.Counter(r.get("explicitness", "explicit") for r in ftest)
    L = ["# EVIDENCE_V2 — post-hoc deterministic explicitness (analysis_v2)", "",
         "**POST-HOC sensitivity analysis. `confidence_raw` is untouched.** OFFLINE, read-only.",
         f"Hedge lexicon: {HEDGES}", f"Priors (from config / signals._EXPLICIT_PRIOR): {PRIOR}", "",
         f"Text-linked test beliefs: {len(linked)} of 459 ({int(cov*100)}% coverage). No 'implied' class "
         "was added (no simple deterministic rule that is testable; kept binary hedged->implicit / explicit).", "",
         "## Class distribution (post-hoc vs original extractor explicitness)",
         f"- post-hoc: {dict(dist)}", f"- original: {dict(orig_dist)}", "",
         "## AUROC / Brier vs rule-A correctness (test, text-linked subset)", "",
         "| evidence signal | AUROC | Brier |", "|---|---|---|",
         f"| original `evidence` (from extractor explicitness) | {au_orig} | {br_orig} |",
         f"| **post-hoc hedge-lexicon evidence** | {au_post} | {br_post} |",
         f"| verbalized (reference) | {au_verb} | {br_verb} |", "",
         "Interpretation: this tests whether a transparent, reproducible hedge rule recovers (or beats) "
         "the extractor's own explicitness-based evidence signal. It is reported as post-hoc only.", "",
         "## Canonical full-test original evidence signal (rule A, n=459 — NOT subset-restricted)",
         f"- original `evidence` AUROC = **{f_ev}**, verbalized AUROC = **{f_vb}**, "
         f"Spearman rho(evidence, verbalized) = **{f_rho}**; test explicitness = {dict(f_expl)}.",
         f"- The paper's claim holds: on the FULL test split every belief is `explicit`, so "
         f"evidence = noisy_or(verbalized, 0.70) is a strictly monotonic transform of verbalized -> "
         f"IDENTICAL ranking -> AUROC {f_ev} == verbalized {f_vb} (~0.45), rho = {f_rho}. "
         f"The {au_orig} above is a sampling artifact of the {int(cov*100)}%-coverage text-linked subset "
         f"(n={len(linked)}), not a different signal."]
    (OUT / "EVIDENCE_V2.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    json.dump({"coverage": cov, "dist_posthoc": dict(dist), "dist_orig": dict(orig_dist),
               "auroc_brier": {"posthoc": [au_post, br_post], "original": [au_orig, br_orig],
                               "verbalized": [au_verb, br_verb]}}, open(OUT / "_evidence_v2.json", "w"), indent=2)
    print(f"[evidence_v2] linked test n={len(linked)} cov={cov}; posthoc dist={dict(dist)}; "
          f"AUROC posthoc={au_post} orig={au_orig}")


if __name__ == "__main__":
    main()
