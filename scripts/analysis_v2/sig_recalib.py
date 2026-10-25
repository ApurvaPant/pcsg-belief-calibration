"""analysis_v2 items 2 & 3 -> results/analysis_v2/STATS.md  (OFFLINE, read-only).

2. Persona-clustered PAIRED bootstrap (test split, 10000 reps, seed 2026): dAUROC =
   self_consistency - verbalized, rule A and rule C; 95% CI + one-sided p.
   Brier(Platt self_consistency) - base-rate Brier, persona-cluster bootstrap CI.
   DeLong test (labelled 'sensitivity only; assumes independent samples').
   QA McNemar: skipped iff only aggregates exist.
3. Leave-one-persona-out Platt over fit+test pooled (48 folds), self_consistency and
   verbalized: out-of-fold Brier, ECE(15 equal-mass), AUROC vs the original fit->test.
"""
from __future__ import annotations
import json, pathlib, sys
import numpy as np
REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))
from pcsg.analysis import calibration as C

RD = REPO / "results" / "runs" / "main_20261015_000553"
OUT = REPO / "results" / "analysis_v2"; OUT.mkdir(parents=True, exist_ok=True)
LAB = [json.loads(l) for l in (RD / "labelled.jsonl").read_text().splitlines()]
RNG = np.random.default_rng(2026)


def arrs(recs, sig, lab="correct_A"):
    rows = [(float(r[sig]), int(r[lab]), r["persona"]) for r in recs if r.get(sig) is not None]
    return (np.array([a for a, _, _ in rows]), np.array([b for _, b, _ in rows]),
            np.array([c for _, _, c in rows]))


def ece_equal_mass(p, y, bins=15):
    p = np.asarray(p, float); y = np.asarray(y, float)
    order = np.argsort(p); p, y = p[order], y[order]
    idx = np.array_split(np.arange(len(p)), bins)
    e = 0.0
    for ix in idx:
        if len(ix):
            e += (len(ix) / len(p)) * abs(p[ix].mean() - y[ix].mean())
    return float(e)


def paired_dauroc(recs, lab, reps=10000):
    pv, yv, gv = arrs(recs, "verbalized", lab)
    ps, ys, gs = arrs(recs, "self_consistency", lab)
    # aligned (same beliefs have both signals here); group by persona
    assert len(pv) == len(ps) and (gv == gs).all()
    personas = np.unique(gv)
    base = C.auroc(ps, ys) - C.auroc(pv, yv)
    deltas = []
    by = {g: np.where(gv == g)[0] for g in personas}
    for _ in range(reps):
        samp = RNG.choice(personas, len(personas), replace=True)
        idx = np.concatenate([by[g] for g in samp])
        try:
            d = C.auroc(ps[idx], ys[idx]) - C.auroc(pv[idx], yv[idx])
            if not np.isnan(d):
                deltas.append(d)
        except Exception:
            pass
    deltas = np.array(deltas)
    return {"observed_dAUROC": round(float(base), 4),
            "ci95": [round(float(np.quantile(deltas, 0.025)), 4), round(float(np.quantile(deltas, 0.975)), 4)],
            "p_one_sided_sc_le_verb": round(float(np.mean(deltas <= 0)), 4), "reps": len(deltas)}


# ── DeLong (fast, two correlated ROC curves on the same labels) ─────────────── #
def _midrank(x):
    J = np.argsort(x); Z = x[J]; N = len(x); T = np.zeros(N)
    i = 0
    while i < N:
        j = i
        while j < N and Z[j] == Z[i]:
            j += 1
        T[i:j] = 0.5 * (i + j - 1) + 1
        i = j
    out = np.empty(N); out[J] = T
    return out


def delong_two(preds, y):
    y = np.asarray(y, int)
    pos = [p[y == 1] for p in preds]; neg = [p[y == 0] for p in preds]
    m, n = len(pos[0]), len(neg[0]); k = len(preds)
    tx = np.array([_midrank(pos[r]) for r in range(k)])
    ty = np.array([_midrank(neg[r]) for r in range(k)])
    tz = np.array([_midrank(np.r_[pos[r], neg[r]]) for r in range(k)])
    aucs = np.array([(tz[r, :m].sum() - m * (m + 1) / 2) / (m * n) for r in range(k)])
    v01 = (tz[:, :m] - tx) / n
    v10 = 1.0 - (tz[:, m:] - ty) / m
    s01 = np.cov(v01); s10 = np.cov(v10)
    S = s01 / m + s10 / n
    S = np.atleast_2d(S)
    L = np.array([1.0, -1.0])
    var = L @ S @ L
    from scipy.stats import norm
    z = (aucs[0] - aucs[1]) / np.sqrt(var) if var > 0 else 0.0
    p = 2 * (1 - norm.cdf(abs(z)))
    return {"auc_sc": round(float(aucs[0]), 4), "auc_verb": round(float(aucs[1]), 4),
            "z": round(float(z), 4), "p_two_sided": float(f"{p:.3e}")}


def loo_platt(recs, sig, lab="correct_A"):
    personas = sorted({r["persona"] for r in recs})
    oof_p, oof_y = [], []
    for held in personas:
        tr = [r for r in recs if r["persona"] != held]
        te = [r for r in recs if r["persona"] == held]
        ptr, ytr, _ = arrs(tr, sig, lab); pte, yte, _ = arrs(te, sig, lab)
        if len(np.unique(ytr)) < 2 or len(pte) == 0:
            continue
        f = C.fit_platt(ptr, ytr)
        oof_p += list(f(pte)); oof_y += list(yte)
    p = np.array(oof_p); y = np.array(oof_y)
    return {"n": len(y), "folds": len(personas), "brier": round(C.brier(p, y), 4),
            "ece15_equalmass": round(ece_equal_mass(p, y, 15), 4), "auroc": round(C.auroc(p, y), 4)}


def main():
    test = [r for r in LAB if r["split"] == "test"]
    fit = [r for r in LAB if r["split"] == "fit"]
    pool = fit + test
    L = ["# STATS — significance & recalibration robustness (analysis_v2)", "",
         "Source: results/runs/main_20261015_000553/labelled.jsonl (read-only). OFFLINE; no API calls.",
         f"test n={len(test)}, fit n={len(fit)}, fit+test personas={len({r['persona'] for r in pool})}.", "",
         "## 2a. Persona-clustered PAIRED bootstrap — dAUROC = self_consistency - verbalized (test, 10000 reps, seed 2026)", ""]
    res = {}
    for lab in ("correct_A", "correct_C"):
        d = paired_dauroc(test, lab)
        res[lab] = d
        L.append(f"- rule {lab[-1]}: observed dAUROC = **{d['observed_dAUROC']}**, 95% CI {d['ci95']}, "
                 f"one-sided p(SC<=verb) = **{d['p_one_sided_sc_le_verb']}** ({d['reps']} reps)")
    # 2b Brier(Platt SC) - base-rate
    ptr, ytr, _ = arrs(fit, "self_consistency"); pte, yte, gte = arrs(test, "self_consistency")
    platt = C.fit_platt(ptr, ytr); pp = np.clip(platt(pte), 0, 1)
    brier_platt = C.brier(pp, yte)
    base_p = yte.mean(); base_brier = float(np.mean((base_p - yte) ** 2))
    personas = np.unique(gte); by = {g: np.where(gte == g)[0] for g in personas}
    diffs = []
    for _ in range(10000):
        samp = RNG.choice(personas, len(personas), replace=True)
        idx = np.concatenate([by[g] for g in samp])
        bp = C.brier(pp[idx], yte[idx]); br = float(np.mean((yte[idx].mean() - yte[idx]) ** 2))
        diffs.append(bp - br)
    diffs = np.array(diffs)
    L += ["", "## 2b. Brier(Platt self_consistency) - base-rate Brier (test; persona-cluster bootstrap CI)",
          f"- Brier(Platt SC) = **{brier_platt:.4f}**, base-rate Brier (p={base_p:.4f}) = **{base_brier:.4f}**",
          f"- difference = **{brier_platt-base_brier:+.4f}**, 95% CI "
          f"[{np.quantile(diffs,0.025):+.4f}, {np.quantile(diffs,0.975):+.4f}] "
          f"(negative = Platt SC beats the base rate)"]
    # 2c DeLong
    dl = delong_two([pte, arrs(test, "verbalized")[0]], yte)
    L += ["", "## 2c. DeLong test — self_consistency vs verbalized AUROC (test)",
          f"- **sensitivity only (assumes independent samples)**: AUC(SC)={dl['auc_sc']}, "
          f"AUC(verb)={dl['auc_verb']}, z={dl['z']}, p(two-sided)={dl['p_two_sided']}"]
    # 2d QA McNemar
    qa = json.loads((RD / "qa_systems.json").read_text())
    has_peritem = any("per_question" in str(k) for k in qa.get("systems", {}).get("pcsg", {}))
    L += ["", "## 2d. QA (264 questions) — exact McNemar + cluster bootstrap",
          "- **SKIPPED: only aggregate QA records exist** (results/runs/main_20261015_000553/qa_systems.json "
          "has per-system accuracy/coverage/sweep but no per-question correctness). Per-item Tier-2 answers "
          "and verdicts were not persisted, so exact McNemar PCSG-vs-RAG / PCSG-vs-full cannot be computed."]
    # Item 3
    L += ["", "## 3. Recalibration robustness — leave-one-persona-out Platt (fit+test pooled, 48 folds)", "",
          "| signal | OOF n | folds | Brier | ECE(15 eq-mass) | AUROC | original fit->test (Platt) |",
          "|---|---|---|---|---|---|---|"]
    cal = json.loads((RD / "calibration.json").read_text())
    for sig in ("self_consistency", "verbalized"):
        lo = loo_platt(pool, sig)
        orig = cal["by_signal_recalibrator"].get(sig, {}).get("platt", {})
        orig_s = f"Brier {orig.get('brier')}, ECE {orig.get('ece')}, AUROC {orig.get('auroc')}"
        L.append(f"| {sig} | {lo['n']} | {lo['folds']} | {lo['brier']} | {lo['ece15_equalmass']} | {lo['auroc']} | {orig_s} |")
    (OUT / "STATS.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    json.dump({"paired": res, "brier_platt_sc": brier_platt, "base_brier": base_brier,
               "delong": dl}, open(OUT / "_stats.json", "w"), indent=2)
    print("[stats] wrote results/analysis_v2/STATS.md")
    print(f"  rule A dAUROC={res['correct_A']['observed_dAUROC']} CI={res['correct_A']['ci95']} p={res['correct_A']['p_one_sided_sc_le_verb']}")
    print(f"  Brier(Platt SC)={brier_platt:.4f} base={base_brier:.4f}  DeLong p={dl['p_two_sided']}")


if __name__ == "__main__":
    main()
