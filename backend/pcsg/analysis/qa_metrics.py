"""STEP 2 — per-system QA metrics for the four-answerer comparison (table T3).

Input `records`: list of
  {system, persona, kind, confidence (0..1), correct (0/1), abstained (0/1),
   is_unknown (0/1), tokens}
where `correct` already encodes "correctly abstained" for unanswerable questions.

Reports, per system: accuracy (overall), selective accuracy (on answered),
coverage, fabrication rate (answered an unanswerable question), AURC
(risk–coverage), tokens/question, and answer-level ECE (confidence vs correctness
on answerable questions). Also the tau sweep for F2.
"""
from __future__ import annotations

from pcsg.analysis import abstention, calibration


def summarize(records: list[dict]) -> dict:
    n = len(records)
    if n == 0:
        return {"n": 0, "accuracy": 0.0, "selective_accuracy": 0.0, "coverage": 0.0,
                "fabrication_rate": 0.0, "aurc": 0.0, "tokens_per_q": 0.0,
                "answer_ece": None, "sweep": []}
    answered = [r for r in records if not r.get("abstained")]
    unknown = [r for r in records if r.get("is_unknown")]
    acc = sum(r["correct"] for r in records) / n
    selective = (sum(r["correct"] for r in answered) / len(answered)) if answered else 0.0
    coverage = len(answered) / n
    fab = [r for r in unknown if not r.get("abstained")]
    fabrication = (len(fab) / len(unknown)) if unknown else 0.0
    tokens_per_q = sum(r.get("tokens", 0) for r in records) / n
    known_ans = [r for r in records if not r.get("is_unknown")]
    ece = (calibration.ece([r["confidence"] for r in known_ans],
                           [r["correct"] for r in known_ans], 10, "uniform")
           if known_ans else None)
    return {
        "n": n,
        "accuracy": round(acc, 4),
        "selective_accuracy": round(selective, 4),
        "coverage": round(coverage, 4),
        "fabrication_rate": round(fabrication, 4),
        "aurc": round(abstention.aurc(records), 4),
        "tokens_per_q": round(tokens_per_q, 1),
        "answer_ece": round(ece, 4) if ece is not None else None,
        "sweep": abstention.sweep(records),
    }


def summarize_all(records_by_system: dict[str, list[dict]]) -> dict:
    return {s: summarize(recs) for s, recs in records_by_system.items()}
