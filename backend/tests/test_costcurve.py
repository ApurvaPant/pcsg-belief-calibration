"""Regression for the F3 cost curve.

F3 "accuracy" is a truth RECALL in [0,1] (distinct ACTIVE truth facts matched by a
retained assertion / total active truth facts), NOT the "fraction written" proxy
that produced accuracy=1.0667 in an early dry run (it counted distinct WRITTEN
facts against the truth denominator, uncapped). These tests pin:
  * every cell's accuracy stays in [0,1], even when the input over-counts;
  * cost is non-increasing as the tier1 threshold rises (looser threshold => more
    messages pass the gate => more cost);
  * write_tau drops low-confidence assertions from the retained set.
"""
from __future__ import annotations

from pcsg.analysis import costcurve


def _cell(cells, name):
    return next(c for c in cells if c["cell"] == name)


def test_accuracy_capped_in_unit_range_when_overcounting():
    # The exact shape of the old bug: ONE persona, truth_count=2, but THREE distinct
    # "correct" fact_ids retained. A recall can never exceed 1 -> capped at 2/2.
    logs = [{"persona": 0, "salience": 90, "tokens": 100, "assertions": [
        {"fact_id": "a", "confidence": 1.0, "correct": 1},
        {"fact_id": "b", "confidence": 1.0, "correct": 1},
        {"fact_id": "c", "confidence": 1.0, "correct": 1},
    ]}]
    out = costcurve.replay(logs, {0: 2})
    for c in out["cells"]:
        assert 0.0 <= c["accuracy"] <= 1.0, c
    assert _cell(out["cells"], "0/off")["accuracy"] == 1.0   # min(3, 2)/2 == 1.0


def test_every_cell_accuracy_in_unit_range_mixed():
    logs = [
        {"persona": 0, "salience": 80, "tokens": 300, "assertions": [
            {"fact_id": "0:t1", "confidence": 0.9, "correct": 1},
            {"fact_id": "0:spur", "confidence": 0.5, "correct": 0}]},
        {"persona": 1, "salience": 30, "tokens": 300, "assertions": [
            {"fact_id": "1:t1", "confidence": 0.7, "correct": 1}]},
    ]
    out = costcurve.replay(logs, {0: 2, 1: 1})
    for c in out["cells"]:
        assert 0.0 <= c["accuracy"] <= 1.0, c
    # persona 0: 1 of 2 matched; persona 1 (salience 30) only counts at threshold<=20
    assert _cell(out["cells"], "0/off")["accuracy"] == round((1 + 1) / 3, 4)
    assert _cell(out["cells"], "40/off")["accuracy"] == round(1 / 3, 4)   # persona 1 gated out


def test_cost_non_increasing_as_threshold_rises():
    logs = [
        {"persona": 0, "salience": 10, "tokens": 100, "assertions": []},
        {"persona": 0, "salience": 50, "tokens": 100, "assertions": []},
        {"persona": 0, "salience": 90, "tokens": 100, "assertions": []},
    ]
    out = costcurve.replay(logs, {0: 1})
    costs = {c["tier1"]: c["cost_tokens"] for c in out["cells"]}
    seq = [costs[t] for t in costcurve.TIER1]       # TIER1 is ascending
    assert seq == sorted(seq, reverse=True)         # non-increasing as threshold rises
    assert costs[0] == 300 and costs[70] == 100     # looser threshold => more cost


def test_write_tau_drops_low_confidence_assertions():
    logs = [{"persona": 0, "salience": 90, "tokens": 100, "assertions": [
        {"fact_id": "0:t1", "confidence": 0.5, "correct": 1},   # dropped at tau 0.6/0.8
        {"fact_id": "0:t2", "confidence": 0.9, "correct": 1}]}]
    out = costcurve.replay(logs, {0: 2})
    assert _cell(out["cells"], "0/off")["accuracy"] == 1.0     # both kept: 2/2
    assert _cell(out["cells"], "0/0.6")["accuracy"] == 0.5     # one dropped: 1/2
    assert _cell(out["cells"], "0/0.8")["accuracy"] == 0.5


def test_pareto_marks_a_frontier():
    logs = [
        {"persona": 0, "salience": 90, "tokens": 50, "assertions": [
            {"fact_id": "0:t1", "confidence": 0.9, "correct": 1}]},
        {"persona": 0, "salience": 10, "tokens": 50, "assertions": [
            {"fact_id": "0:t2", "confidence": 0.9, "correct": 1}]},
    ]
    out = costcurve.replay(logs, {0: 2})
    assert any(c["pareto"] for c in out["cells"])              # at least one frontier cell
    for c in out["cells"]:
        assert 0.0 <= c["accuracy"] <= 1.0
