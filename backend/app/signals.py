"""STEP 0 — per-assertion confidence signals (append-only `confidence_signals`).

Signals stored per stored assertion:
  verbalized        = the extractor's own confidence (0-1).
  evidence          = Noisy-OR of verbalized with an explicitness prior (an
                      explicit statement is stronger evidence than an implicit one).
  self_consistency  = agreement of k extra structuring (FILL) samples at
                      temperature 0.7 on (subject, dimension, normalised value,
                      operation), equivalent keys treated as agreeing. Computed
                      only when enabled (it costs k paid calls); null otherwise.
  intensity         = null unless an intensity was extracted (not extracted today).

logprob is intentionally skipped. The table is append-only (INV-02).
"""
from __future__ import annotations

import os

from . import ontology
from .db import now_iso
from .update import noisy_or
from .utils import new_id

# Explicitness -> prior probability the assertion is a real durable fact.
_EXPLICIT_PRIOR = {"explicit": 0.70, "implicit": 0.40}


def self_consistency_enabled() -> bool:
    """k=3 resampling is paid, so it runs only when explicitly enabled (STEP 3)."""
    return os.getenv("PCSG_SELF_CONSISTENCY") == "1"


def evidence_signal(verbalized: float, explicitness: str) -> float:
    prior = _EXPLICIT_PRIOR.get(explicitness, 0.5)
    return noisy_or(float(verbalized), prior)


def _sig(a: dict) -> tuple:
    """Agreement key for self-consistency: subject-kind + dimension + normalised
    value + operation. The node KEY is deliberately excluded so equivalent keys
    (going_to_gym / working_out) count as agreeing."""
    subj = a.get("subject")
    subj_kind = "self" if subj == "self" else "entity"
    return (subj_kind, a.get("dimension"), ontology.normalize_value(str(a.get("value", ""))),
            str(a.get("operation", "ADD")).upper())


def assertion_sig(a: dict) -> tuple:
    """Public agreement key for an assertion (see _sig)."""
    return _sig(a)


def compute_self_consistency(extract_fn, message, context, kept, k: int = 3) -> dict[tuple, float]:
    """Return {assertion_sig: agreement_fraction} over k resamples. `extract_fn`
    takes (message, context, temperature) and returns a list of assertion dicts
    (so this module never imports the extractor directly / avoids a cycle)."""
    targets = {_sig(a) for a in kept}
    hits = {t: 0 for t in targets}
    for _ in range(k):
        sample = extract_fn(message, context, 0.7)
        sample_sigs = {_sig(a) for a in sample}
        for t in targets:
            if t in sample_sigs:
                hits[t] += 1
    return {t: round(hits[t] / k, 3) for t in targets}


def write_signals(conn, node_id, message_id, assertion) -> None:
    """Append one confidence_signals row for a stored assertion.

    self_consistency is never a *silent* null: whenever it is absent we record a
    reason code (`self_consistency_reason`) so T2 can account for coverage. The
    reason is `sc_disabled` when resampling is off, or `sc_unattached` if an
    assertion reached persistence without a computed score (a bug to chase, not
    to hide)."""
    try:
        verbalized = max(0.0, min(1.0, float(assertion.get("confidence", 0.7))))
    except (TypeError, ValueError):
        verbalized = 0.7
    expl = str(assertion.get("explicitness", "explicit"))
    sc = assertion.get("self_consistency")
    reason = None
    if sc is None:
        reason = "sc_disabled" if not self_consistency_enabled() else "sc_unattached"
    intensity = assertion.get("intensity")
    conn.execute(
        "INSERT INTO confidence_signals (id, node_id, message_id, dimension, key, value, "
        "explicitness, verbalized, self_consistency, self_consistency_reason, evidence, "
        "intensity, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (new_id("sig"), node_id, message_id, assertion.get("dimension"), assertion.get("key"),
         assertion.get("value"), expl, verbalized,
         float(sc) if sc is not None else None, reason,
         evidence_signal(verbalized, expl),
         float(intensity) if intensity is not None else None, now_iso()),
    )


def coverage(conn) -> dict:
    """Per-signal coverage over confidence_signals: fraction of rows with a
    non-null value. verbalized/evidence are always present; self_consistency is
    present whenever resampling was enabled and the score attached."""
    rows = conn.execute(
        "SELECT verbalized, self_consistency, evidence FROM confidence_signals").fetchall()
    n = len(rows) or 1
    def frac(col):
        return round(sum(1 for r in rows if r[col] is not None) / n, 4)
    return {"n": len(rows),
            "verbalized": frac("verbalized"),
            "self_consistency": frac("self_consistency"),
            "evidence": frac("evidence")}
