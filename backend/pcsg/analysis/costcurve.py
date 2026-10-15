"""Write-policy cost curve (STEP 6 -> F3), offline replay over the gate-open run.

For each (tier1_threshold, write_tau): drop messages whose salience < threshold,
drop assertions whose confidence < write_tau, then recompute current-state
accuracy and cost (tokens of the messages that passed the gate). This is an
OFFLINE replay — no model calls.

"accuracy" here is a truth RECALL, NOT QA accuracy and NOT assertion precision:
distinct ACTIVE truth-table facts that a retained assertion matches (rule A:
dimension + normalised value) / total active truth facts, per persona, summed.
It is bounded in [0,1] by construction — see `_state_accuracy`.

Input `logs`: list of message dicts:
  {persona, salience (0-100), tokens (int, write-path tokens for the message),
   assertions: [{fact_id, confidence, correct (0/1)}]}
  where, for the recall to be meaningful, `correct`==1 iff the assertion matched a
  truth fact and `fact_id` is that matched TRUTH fact's id.
`truth_counts`: {persona: number_of_active_truth_facts}.
"""
from __future__ import annotations

TIER1 = [0, 20, 40, 60, 70]
WRITE_TAU = {"off": 0.0, "0.6": 0.6, "0.8": 0.8}


def _state_accuracy(logs, threshold, wtau, truth_counts):
    # Truth RECALL: distinct correctly-retained truth facts per persona / total
    # truth facts. `correct` must mean "matched a truth fact" and `fact_id` must be
    # the matched TRUTH fact's id (set that way upstream), so distinct ids per
    # persona <= that persona's truth count. The per-persona min(...) cap enforces
    # that invariant defensively: a recall can never exceed 1, so even malformed
    # input (e.g. counting WRITTEN facts, the old accuracy=1.0667 bug) stays in
    # [0,1]. The numerator is summed over the SAME personas as the denominator.
    correct_facts = {}       # persona -> set(fact_id) of correct, retained truth facts
    for m in logs:
        if m["salience"] < threshold:
            continue
        for a in m.get("assertions", []):
            if a["confidence"] < wtau:
                continue
            if a.get("correct"):
                correct_facts.setdefault(m["persona"], set()).add(a["fact_id"])
    total = sum(truth_counts.values()) or 1
    got = sum(min(len(correct_facts.get(p, ())), n) for p, n in truth_counts.items())
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
