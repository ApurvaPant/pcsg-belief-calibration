"""Calibration study (STEP 6 -> F1, T2).

Metrics: ECE (equal-width 10 bins + equal-mass 15 bins), Brier, AUROC, with 95%
bootstrap CIs resampling PERSONAS. Recalibrators: temperature, Platt, isotonic —
fit on `fit` personas, evaluated on `test` personas only. Reliability bins
(before/after) feed F1.

Input `records`: list of dicts with keys: persona, split ('fit'|'test'|'dev'),
correct (0/1), and signal values (verbalized, self_consistency, evidence).
"""
from __future__ import annotations

import numpy as np
from scipy.optimize import minimize_scalar
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score

SIGNALS = ("verbalized", "self_consistency", "evidence")
_EPS = 1e-6


def _clip(p):
    return np.clip(np.asarray(p, float), _EPS, 1 - _EPS)


def ece(probs, labels, bins=10, strategy="uniform"):
    p = _clip(probs); y = np.asarray(labels, float)
    if strategy == "quantile":
        edges = np.unique(np.quantile(p, np.linspace(0, 1, bins + 1)))
    else:
        edges = np.linspace(0, 1, bins + 1)
    e = 0.0
    for i in range(len(edges) - 1):
        lo, hi = edges[i], edges[i + 1]
        m = (p >= lo) & (p <= hi if i == len(edges) - 2 else p < hi)
        if m.sum() == 0:
            continue
        e += (m.sum() / len(p)) * abs(p[m].mean() - y[m].mean())
    return float(e)


def brier(probs, labels):
    p = _clip(probs); y = np.asarray(labels, float)
    return float(np.mean((p - y) ** 2))


def mce(probs, labels, bins=10):
    """Maximum Calibration Error: largest |acc − conf| over equal-width bins."""
    p = _clip(probs); y = np.asarray(labels, float)
    edges = np.linspace(0, 1, bins + 1)
    worst = 0.0
    for i in range(bins):
        lo, hi = edges[i], edges[i + 1]
        m = (p >= lo) & (p <= hi if i == bins - 1 else p < hi)
        if m.sum():
            worst = max(worst, abs(p[m].mean() - y[m].mean()))
    return float(worst)


def nll(probs, labels):
    """Mean negative log-likelihood (log loss) of the Bernoulli predictions."""
    p = _clip(probs); y = np.asarray(labels, float)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def auroc(probs, labels):
    y = np.asarray(labels, int)
    if len(np.unique(y)) < 2:
        return float("nan")
    return float(roc_auc_score(y, np.asarray(probs, float)))


def reliability_bins(probs, labels, bins=10):
    p = _clip(probs); y = np.asarray(labels, float)
    edges = np.linspace(0, 1, bins + 1)
    out = []
    for i in range(bins):
        lo, hi = edges[i], edges[i + 1]
        m = (p >= lo) & (p <= hi if i == bins - 1 else p < hi)
        if m.sum():
            out.append({"bin": i, "conf": float(p[m].mean()), "acc": float(y[m].mean()), "n": int(m.sum())})
    return out


# ── recalibrators (fit on fit split, apply to test) ────────────────────────#
def _logit(p):
    p = _clip(p)
    return np.log(p / (1 - p))


def fit_temperature(probs, labels):
    z = _logit(probs); y = np.asarray(labels, float)

    def nll(T):
        q = 1 / (1 + np.exp(-z / max(T, 1e-3)))
        q = _clip(q)
        return -np.mean(y * np.log(q) + (1 - y) * np.log(1 - q))

    r = minimize_scalar(nll, bounds=(0.05, 20), method="bounded")
    T = float(r.x)
    return lambda p: 1 / (1 + np.exp(-_logit(p) / T))


def fit_platt(probs, labels):
    x = _logit(probs).reshape(-1, 1); y = np.asarray(labels, int)
    lr = LogisticRegression().fit(x, y)
    return lambda p: lr.predict_proba(_logit(p).reshape(-1, 1))[:, 1]


def fit_isotonic(probs, labels):
    iso = IsotonicRegression(out_of_bounds="clip").fit(np.asarray(probs, float), np.asarray(labels, float))
    return lambda p: iso.predict(np.asarray(p, float))


RECALIBRATORS = {"raw": None, "temperature": fit_temperature, "platt": fit_platt, "isotonic": fit_isotonic}


def bootstrap_ci(metric_fn, probs, labels, groups, n=500, alpha=0.05, seed=0):
    rng = np.random.default_rng(seed)
    probs = np.asarray(probs, float); labels = np.asarray(labels, float); groups = np.asarray(groups)
    uniq = np.unique(groups)
    vals = []
    for _ in range(n):
        samp = rng.choice(uniq, size=len(uniq), replace=True)
        idx = np.concatenate([np.where(groups == g)[0] for g in samp])
        try:
            v = metric_fn(probs[idx], labels[idx])
            if not np.isnan(v):
                vals.append(v)
        except Exception:  # noqa: BLE001
            pass
    if not vals:
        return [float("nan"), float("nan")]
    return [float(np.quantile(vals, alpha / 2)), float(np.quantile(vals, 1 - alpha / 2))]


def study(records, n_boot=300):
    fit = [r for r in records if r.get("split") == "fit"]
    test = [r for r in records if r.get("split") == "test"]
    out = {"n_fit": len(fit), "n_test": len(test), "by_signal_recalibrator": {}, "reliability": {}}
    for sig in SIGNALS:
        fp = np.array([r[sig] for r in fit if r.get(sig) is not None], float)
        fy = np.array([r["correct"] for r in fit if r.get(sig) is not None], float)
        tp = np.array([r[sig] for r in test if r.get(sig) is not None], float)
        ty = np.array([r["correct"] for r in test if r.get(sig) is not None], float)
        tg = np.array([r["persona"] for r in test if r.get(sig) is not None])
        if len(tp) == 0:
            continue
        out["by_signal_recalibrator"][sig] = {}
        out["reliability"][sig] = {}
        for rec, fitter in RECALIBRATORS.items():
            apply = (lambda p: p) if fitter is None or len(fp) < 5 else fitter(fp, fy)
            q = np.asarray(apply(tp), float)
            out["by_signal_recalibrator"][sig][rec] = {
                "ece": round(ece(q, ty, 10, "uniform"), 4),
                "ece_quantile": round(ece(q, ty, 15, "quantile"), 4),
                "mce": round(mce(q, ty, 10), 4),
                "brier": round(brier(q, ty), 4),
                "nll": round(nll(q, ty), 4),
                "auroc": round(auroc(q, ty), 4),
                "ci": {"ece": [round(x, 4) for x in bootstrap_ci(lambda p, y: ece(p, y), q, ty, tg, n_boot)]},
            }
            if rec in ("raw", "temperature"):
                out["reliability"][sig]["before" if rec == "raw" else "after"] = reliability_bins(q, ty)
    return out
