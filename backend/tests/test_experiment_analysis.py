"""Offline verification of the experiment analysis (STEP 6 VERIFY requirements).
No model calls — pure synthetic + oracle checks."""
from __future__ import annotations

import numpy as np

from pcsg import oracle
from pcsg.analysis import abstention, calibration, costcurve


# ── calibration ────────────────────────────────────────────────────────────#
def test_ece_reference_single_bin():
    # All confidence 0.9, accuracy 0.7 -> ECE == |0.9-0.7| == 0.2 exactly.
    rng = np.random.default_rng(0)
    probs = np.full(4000, 0.9)
    labels = (rng.random(4000) < 0.70).astype(float)
    assert abs(calibration.ece(probs, labels, 10, "uniform") - 0.20) < 0.02


def test_perfect_profile_low_ece():
    rng = np.random.default_rng(1)
    probs = rng.random(4000)
    labels = (rng.random(4000) < probs).astype(float)  # perfectly calibrated by construction
    assert calibration.ece(probs, labels, 10, "uniform") < 0.05


def test_recalibration_reduces_ece():
    rng = np.random.default_rng(2)
    # overconfident: reports 0.9, correct ~0.7
    probs = np.full(3000, 0.9)
    labels = (rng.random(3000) < 0.70).astype(float)
    before = calibration.ece(probs, labels, 10, "uniform")
    iso = calibration.fit_isotonic(probs, labels)
    after = calibration.ece(iso(probs), labels, 10, "uniform")
    assert after < before and after < 0.1


def test_oracle_miscalibration_recovered():
    # Drive the oracle's overconfident profile and recover ECE ~ 0.2 from the
    # (verbalized, correct) pairs — oracle+calibration validated together.
    oracle.configure("overconfident", seed=7)
    truth = {"dimension": "preference", "key": "food_likes", "value": "ramen",
             "operation": "ADD", "subject": "self", "explicitness": "explicit"}
    confs, correct = [], []
    for i in range(3000):
        out, conf, ok = oracle._decide(dict(truth, value=f"ramen{i}"), resample_i=0)
        confs.append(conf); correct.append(1 if ok else 0)
    acc = np.mean(correct)
    assert abs(acc - 0.70) < 0.04                       # oracle is correct ~70%
    assert abs(np.mean(confs) - 0.90) < 1e-6            # reports 0.90
    assert abs(calibration.ece(confs, correct, 10, "uniform") - 0.20) < 0.04


# ── abstention ──────────────────────────────────────────────────────────────#
def test_abstention_coverage_monotonic_and_aurc():
    rng = np.random.default_rng(3)
    recs = []
    for _ in range(500):
        c = rng.random()
        recs.append({"confidence": c, "correct": int(rng.random() < c), "is_unknown": False})
    sweep = abstention.sweep(recs)
    covs = [r["coverage"] for r in sweep]
    assert all(covs[i] >= covs[i + 1] - 1e-9 for i in range(len(covs) - 1))  # non-increasing
    # confidence tracks correctness -> AURC should be low (< random 0.5-ish)
    assert abstention.aurc(recs) < 0.3


def test_abstention_fabrication_drops_with_tau():
    # unknown questions answered only when confidence >= tau
    recs = [{"confidence": 0.95, "correct": 0, "is_unknown": True} for _ in range(20)]
    recs += [{"confidence": 0.5, "correct": 1, "is_unknown": False} for _ in range(20)]
    sweep = abstention.sweep(recs)
    fab0 = next(r["fabrication"] for r in sweep if r["tau"] == 0.0)
    fab_hi = next(r["fabrication"] for r in sweep if r["tau"] == 1.0)
    assert fab0 == 1.0 and fab_hi == 0.0


# ── cost curve ──────────────────────────────────────────────────────────────#
def _logs():
    logs = []
    for pid in range(5):
        for j in range(10):
            sal = (j * 10) % 100
            logs.append({"persona": pid, "salience": sal, "tokens": 100,
                         "assertions": [{"fact_id": f"{pid}_{j}", "confidence": 0.7 if j % 2 else 0.9,
                                         "correct": 1}]})
    return logs, {pid: 10 for pid in range(5)}


def test_costcurve_monotonic_and_reproduction():
    logs, truth = _logs()
    res = costcurve.replay(logs, truth)
    # cost non-increasing as tier1 threshold rises (within write_tau=off)
    off = [c for c in res["cells"] if c["write_tau"] == "off"]
    off.sort(key=lambda c: c["tier1"])
    costs = [c["cost_tokens"] for c in off]
    assert all(costs[i] >= costs[i + 1] for i in range(len(costs) - 1))
    # (0, off) retains all correct facts -> accuracy 1.0 (reproduces full run)
    base = next(c for c in off if c["tier1"] == 0)
    assert base["accuracy"] == 1.0
