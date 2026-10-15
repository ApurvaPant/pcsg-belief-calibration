"""Label stored assertions against a persona truth table (STEP 6).

Deterministic match first (same dimension + normalised value, equivalent keys
within a dimension accepted), then P7 value-equivalence (mocked in dry-run as a
small synonym map). An assertion with no truth-table counterpart is labelled 0
(spurious). Returns per-assertion records for the calibration study.
"""
from __future__ import annotations

import re

from app import ontology

# P7 stand-in for dry-run: equivalent values. Live runs replace this with a judge.
_SYNONYMS = {"nyc": "new york", "sf": "san francisco", "veg": "vegetarian"}


def _norm(v: str) -> str:
    v = ontology.normalize_value(str(v or ""))
    return _SYNONYMS.get(v, v)


def label_one(stored: dict, truth_facts: list[dict]) -> tuple[int, str | None]:
    """Label A (DEFAULT): same dimension + normalised value (equivalent keys accepted)."""
    sd, sv = stored.get("dimension"), _norm(stored.get("value"))
    for f in truth_facts:
        if f["dimension"] == sd and _norm(f["value"]) == sv:   # equiv keys accepted (key ignored)
            return 1, f.get("id")
    return 0, None


# ── optional looser label definitions (C, E); A stays the default ──────────── #
def _fuzzy_C(av: str, tv: str) -> bool:
    a, b = _norm(av), _norm(tv)
    if a and b and (a in b or b in a):
        return True
    ta, tb = set(a.split()), set(b.split())
    return bool(ta and tb and len(ta & tb) / max(len(ta), len(tb)) >= 0.5)


_LEAD = re.compile(r"^(become an?|learn to|want to|wanna|aim to|planning to|going to|i am|i'm|i|my|to)\s+", re.I)
_LEMMA = {"coding": "code", "sketching": "sketch", "running": "run", "cooking": "cook", "painting": "paint",
          "gaming": "game", "reading": "read", "writing": "write", "swimming": "swim", "programming": "program"}
_NUMRANGE = re.compile(r"^(\d+)[-to]+(\d+)$")
_DECADE = re.compile(r"^(\d+)s$")
_E_COMPAT = {"skill", "habit", "interest"}


def _lemma(t: str) -> str:
    if t in _LEMMA:
        return _LEMMA[t]
    return t[:-3] if len(t) > 4 and t.endswith("ing") else t


def _norm_E(v: str) -> str:
    s = re.sub(r"\s+", " ", str(v or "").lower().replace("_", " ").replace("-", " ")).strip()
    prev = None
    while prev != s:
        prev = s
        s = _LEAD.sub("", s)
    return " ".join(_lemma(t) for t in s.split())


def _num_match_E(a: str, b: str) -> bool:
    na, nb = _norm_E(a), _norm_E(b)
    for x, y in ((na if na.isdigit() else None, nb), (nb if nb.isdigit() else None, na)):
        if x is None:
            continue
        n = int(x); yy = y.replace(" ", "")
        m = _NUMRANGE.match(yy)
        if m and int(m.group(1)) <= n <= int(m.group(2)):
            return True
        m = _DECADE.match(yy)
        if m and (n // 10) * 10 == int(m.group(1)):
            return True
    return False


def _match_E(av: str, tv: str) -> bool:
    a, b = _norm_E(av), _norm_E(tv)
    if not a or not b:
        return False
    if a == b or a in b or b in a:
        return True
    ta, tb = set(a.split()), set(b.split())
    if ta and tb and len(ta & tb) / max(len(ta), len(tb)) >= 0.5:
        return True
    return _num_match_E(av, tv)


def label_C(stored: dict, truth_facts: list[dict]) -> int:
    """Label C: value match ignoring dimension, plus fuzzy (containment / token-overlap>=0.5)."""
    sv = _norm(stored.get("value"))
    for f in truth_facts:
        if _norm(f["value"]) == sv or _fuzzy_C(f["value"], stored.get("value")):
            return 1
    return 0


def label_E(stored: dict, truth_facts: list[dict], *, fact_only: bool = False) -> int:
    """Label E: normalised (lead-phrase strip + lemmatise) match with number range/decade.
    fact_only=True -> fact_correct (any dimension); else fully_correct (same dimension,
    with skill/habit/interest mutually compatible)."""
    sd = stored.get("dimension")
    for f in truth_facts:
        if not _match_E(f["value"], stored.get("value")):
            continue
        if fact_only:
            return 1
        same = (f["dimension"] == sd) or (f["dimension"] in _E_COMPAT and sd in _E_COMPAT)
        if same:
            return 1
    return 0


def labels_all(stored: dict, truth_facts: list[dict]) -> dict:
    """All label definitions side by side for one assertion."""
    a, _ = label_one(stored, truth_facts)
    return {"A": a, "C": label_C(stored, truth_facts),
            "E": label_E(stored, truth_facts), "E_fact": label_E(stored, truth_facts, fact_only=True)}


def label_run(stored_by_persona: dict, truth_by_persona: dict, split_by_persona: dict) -> list[dict]:
    """stored_by_persona: {pid: [assertion dicts incl. signals + dimension/value]}
    truth_by_persona:  {pid: [truth fact dicts {id,dimension,value}]}
    Returns calibration records: {persona, split, correct, verbalized, self_consistency,
    evidence, dimension, explicitness, operation}."""
    records = []
    for pid, stored in stored_by_persona.items():
        truth = truth_by_persona.get(pid, [])
        for a in stored:
            labs = labels_all(a, truth)   # A (default), C, E, E_fact side by side
            records.append({
                "persona": pid, "split": split_by_persona.get(pid, "fit"),
                "correct": labs["A"],       # DEFAULT label = A (unchanged)
                "correct_A": labs["A"], "correct_C": labs["C"],
                "correct_E": labs["E"], "correct_E_fact": labs["E_fact"],
                "verbalized": a.get("verbalized"), "self_consistency": a.get("self_consistency"),
                "evidence": a.get("evidence"), "dimension": a.get("dimension"),
                "explicitness": a.get("explicitness", "explicit"), "operation": a.get("operation", "ADD"),
            })
    return records
