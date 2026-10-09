"""Write-policy cost curve (STEP 6 -> F3), offline replay over the gate-open run.

For each (tier1_threshold, write_tau): drop messages whose salience < threshold,
drop assertions whose confidence < write_tau, then recompute current-state
accuracy (distinct correct truth-table facts retained / total truth facts) and
cost (tokens of the messages that passed the gate). This is an OFFLINE replay —
no model calls.

Input `logs`: list of message dicts:
  {persona, salience (0-100), tokens (int, total stage tokens for the message),
   assertions: [{fact_id, confidence, correct (0/1)}]}
`truth_counts`: {persona: number_of_truth_facts}.
"""
from __future__ import annotations

TIER1 = [0, 20, 40, 60, 70]
WRITE_TAU = {"off": 0.0, "0.6": 0.6, "0.8": 0.8}


def _state_accuracy(logs, threshold, wtau, truth_counts):
    # distinct correctly-retained facts per persona / total truth facts
    correct_facts = {}       # persona -> set(fact_id) correctly written
    for m in logs:
        if m["salience"] < threshold:
            continue
        for a in m.get("assertions", []):
            if a["confidence"] < wtau:
                continue
            if a.get("correct"):
                correct_facts.setdefault(m["persona"], set()).add(a["fact_id"])
    total = sum(truth_counts.values()) or 1
    got = sum(len(s) for s in correct_facts.values())
    return got / total


def _cost(logs, threshold):
    return sum(m["tokens"] for m in logs if m["salience"] >= threshold)


def replay(logs, truth_counts):
    cells = []
    for t in TIER1:
        for name, wtau in WRITE_TAU.items():
            cells.append({
                "cell": f"{t}/{name}", "tier1": t, "write_tau": name,
                "accuracy": round(_state_accuracy(logs, t, wtau, truth_counts), 4),
                "cost_tokens": _cost(logs, t),
                "ece": None,   # filled by the caller if calibration is joined
                "pareto": False,
            })
    _mark_pareto(cells)
    return {"cells": cells, "tier1_levels": TIER1, "write_tau_levels": list(WRITE_TAU)}


def _mark_pareto(cells):
    # Pareto-optimal = no other cell has >= accuracy AND <= cost (strictly better).
    for c in cells:
        dominated = any(o is not c and o["accuracy"] >= c["accuracy"]
                        and o["cost_tokens"] <= c["cost_tokens"]
                        and (o["accuracy"] > c["accuracy"] or o["cost_tokens"] < c["cost_tokens"])
                        for o in cells)
        c["pareto"] = not dominated
