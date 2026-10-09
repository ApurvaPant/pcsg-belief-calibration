"""Label stored assertions against a persona truth table (STEP 6).

Deterministic match first (same dimension + normalised value, equivalent keys
within a dimension accepted), then P7 value-equivalence (mocked in dry-run as a
small synonym map). An assertion with no truth-table counterpart is labelled 0
(spurious). Returns per-assertion records for the calibration study.
"""
from __future__ import annotations

from app import ontology

# P7 stand-in for dry-run: equivalent values. Live runs replace this with a judge.
_SYNONYMS = {"nyc": "new york", "sf": "san francisco", "veg": "vegetarian"}


def _norm(v: str) -> str:
    v = ontology.normalize_value(str(v or ""))
    return _SYNONYMS.get(v, v)


def label_one(stored: dict, truth_facts: list[dict]) -> tuple[int, str | None]:
    sd, sv = stored.get("dimension"), _norm(stored.get("value"))
    for f in truth_facts:
        if f["dimension"] == sd and _norm(f["value"]) == sv:   # equiv keys accepted (key ignored)
            return 1, f.get("id")
    return 0, None


def label_run(stored_by_persona: dict, truth_by_persona: dict, split_by_persona: dict) -> list[dict]:
    """stored_by_persona: {pid: [assertion dicts incl. signals + dimension/value]}
    truth_by_persona:  {pid: [truth fact dicts {id,dimension,value}]}
    Returns calibration records: {persona, split, correct, verbalized, self_consistency,
    evidence, dimension, explicitness, operation}."""
    records = []
    for pid, stored in stored_by_persona.items():
        truth = truth_by_persona.get(pid, [])
        for a in stored:
            correct, _ = label_one(a, truth)
            records.append({
                "persona": pid, "split": split_by_persona.get(pid, "fit"), "correct": correct,
                "verbalized": a.get("verbalized"), "self_consistency": a.get("self_consistency"),
                "evidence": a.get("evidence"), "dimension": a.get("dimension"),
                "explicitness": a.get("explicitness", "explicit"), "operation": a.get("operation", "ADD"),
            })
    return records
