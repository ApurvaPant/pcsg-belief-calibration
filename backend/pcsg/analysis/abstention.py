"""Abstention study (STEP 6 -> F2).

Sweep abstain_tau in [0,1] step 0.05: answer a question only when the chosen
confidence signal >= tau, else abstain. Reports coverage, accuracy on answered,
risk-coverage AURC, and fabrication rate on never-mentioned facts (answering an
unknown = a fabrication). Compares raw vs calibrated confidence.

Input `records`: list of {confidence, correct (0/1), is_unknown (bool)}.
For an unknown question, `correct` means "correctly abstained / said don't know".
"""
from __future__ import annotations

import numpy as np

TAUS = [round(0.05 * i, 2) for i in range(21)]  # 0.00 .. 1.00


def sweep(records, signal_key="confidence"):
    rows = []
    n = len(records)
    for tau in TAUS:
        answered = [r for r in records if r[signal_key] >= tau]
        cov = len(answered) / n if n else 0.0
        # accuracy on answered non-unknown questions
        ans_known = [r for r in answered if not r.get("is_unknown")]
        acc = (sum(r["correct"] for r in ans_known) / len(ans_known)) if ans_known else 1.0
        # fabrication: an unknown question that was ANSWERED (not abstained)
        unknowns = [r for r in records if r.get("is_unknown")]
        fab = (sum(1 for r in unknowns if r[signal_key] >= tau) / len(unknowns)) if unknowns else 0.0
        rows.append({"tau": tau, "coverage": round(cov, 4), "accuracy": round(acc, 4),
                     "risk": round(1 - acc, 4), "fabrication": round(fab, 4)})
    return rows


def aurc(records, signal_key="confidence"):
    """Area under the risk-coverage curve (lower is better). Sort by confidence
    desc, sweep coverage 1->0, integrate risk over coverage."""
    rs = sorted(records, key=lambda r: r[signal_key], reverse=True)
    known = [r for r in rs if not r.get("is_unknown")]
    if not known:
        return 0.0
    risks, covs = [], []
    correct = 0
    for i, r in enumerate(known, 1):
        correct += r["correct"]
        covs.append(i / len(known))
        risks.append(1 - correct / i)
    trap = getattr(np, "trapezoid", np.trapz)
    return float(trap(risks, covs))
