"""STEP 4 report (overnight 2026-10-25): compare the SECOND extraction model
(openai/gpt-oss-20b, Nemotron 3 Super withdrawn) against the STORED Nemotron
numbers for the SAME 12 test personas. Per-signal accuracy / ECE / Brier / AUROC
(rule A), persona-paired bootstrap 95% CIs, and the key question:

    does verbalized < self_consistency (AUROC) still hold under gpt-oss?

Reads ONLY: results/runs/<gpt_run>/labelled.jsonl (new) and the main run's
labelled.jsonl (read-only). No model calls. Writes results/model2/<gpt_run>/MODEL2.md.

  python scripts/analysis/model2_compare.py <gpt_run_id> [main_run_id]
"""
from __future__ import annotations
import json, pathlib, sys
import numpy as np

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))
from pcsg.analysis import calibration as C

GPT_RUN = sys.argv[1] if len(sys.argv) > 1 else "model2_sc"
MAIN_RUN = sys.argv[2] if len(sys.argv) > 2 else "main_20261015_000553"
SEL = json.loads((REPO / "results" / "overnight_personas.json").read_text())["selected_12"]
LABEL = "model: gpt-oss-20b (Nemotron 3 Super withdrawn from the hosted API on 2026-10-25)"
SIGNALS = ["verbalized", "self_consistency"]
MET = {"accuracy": lambda p, y: float(np.mean(y)),  # coverage-independent: mean correctness
       "ece": lambda p, y: C.ece(p, y, 10), "brier": C.brier, "auroc": C.auroc}


def _load(run_id):
    p = REPO / "results" / "runs" / run_id / "labelled.jsonl"
    recs = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []
    return [r for r in recs if r.get("persona") in SEL]


def _signal_rows(recs, sig):
    rows = [(float(r[sig]), int(r.get("correct_A", r.get("correct", 0))), r["persona"])
            for r in recs if r.get(sig) is not None]
    return rows


def _metrics(recs, sig):
    rows = _signal_rows(recs, sig)
    if not rows:
        return None
    p = np.array([a for a, _, _ in rows]); y = np.array([b for _, b, _ in rows]); g = np.array([c for _, _, c in rows])
    out = {"n": len(rows)}
    for name, fn in MET.items():
        try:
            val = fn(p, y)
        except Exception:
            val = float("nan")
        ci = C.bootstrap_ci(fn, p, y, g, n=1000, seed=2026)
        out[name] = {"val": round(float(val), 4), "ci": [round(ci[0], 4), round(ci[1], 4)]}
    return out


def _paired_auroc_gap(recs, seed=2026, n=1000):
    """Persona-paired bootstrap of AUROC(self_consistency) - AUROC(verbalized)."""
    vb = {r["persona"]: [] for r in recs}
    sc = {r["persona"]: [] for r in recs}
    for r in recs:
        if r.get("verbalized") is not None:
            vb[r["persona"]].append((float(r["verbalized"]), int(r.get("correct_A", 0))))
        if r.get("self_consistency") is not None:
            sc[r["persona"]].append((float(r["self_consistency"]), int(r.get("correct_A", 0))))
    personas = sorted(set(SEL) & set(p for p in vb))
    rng = np.random.default_rng(seed)
    gaps = []
    for _ in range(n):
        samp = rng.choice(personas, size=len(personas), replace=True)
        vp = [x for pid in samp for x in vb.get(pid, [])]
        sp = [x for pid in samp for x in sc.get(pid, [])]
        try:
            a_v = C.auroc([a for a, _ in vp], [b for _, b in vp])
            a_s = C.auroc([a for a, _ in sp], [b for _, b in sp])
            if not (np.isnan(a_v) or np.isnan(a_s)):
                gaps.append(a_s - a_v)
        except Exception:
            pass
    if not gaps:
        return None
    return {"median_gap": round(float(np.median(gaps)), 4),
            "ci": [round(float(np.quantile(gaps, 0.025)), 4), round(float(np.quantile(gaps, 0.975)), 4)],
            "p_gap_gt_0": round(float(np.mean(np.array(gaps) > 0)), 3)}


def _acc_abc(recs):
    n = len(recs) or 1
    return {k: round(sum(r.get(f"correct_{k}", 0) for r in recs) / n, 4) for k in ("A", "C", "E")}


def main():
    gpt = _load(GPT_RUN); main_ = _load(MAIN_RUN)
    out = REPO / "results" / "model2" / GPT_RUN
    out.mkdir(parents=True, exist_ok=True)
    L = [f"# T-MODEL2 — second extraction model (STEP 4)", "",
         f"**{LABEL}**", "",
         f"Same 12 test personas (seed 2026): {SEL}", "",
         f"Assertions stored: gpt-oss-20b = {len(gpt)} | Nemotron (main, same 12) = {len(main_)}", "",
         f"Rule-A/C/E accuracy — gpt-oss: {_acc_abc(gpt)} | Nemotron: {_acc_abc(main_)}", "",
         "## Per-signal calibration (rule A; persona-paired 95% CI)", "",
         "| model | signal | n | accuracy | ECE | Brier | AUROC [95% CI] |",
         "|---|---|---|---|---|---|---|"]
    for tag, recs in (("gpt-oss-20b", gpt), ("Nemotron(main)", main_)):
        for sig in SIGNALS:
            m = _metrics(recs, sig)
            if not m:
                L.append(f"| {tag} | {sig} | 0 | — | — | — | — |"); continue
            au = m["auroc"]
            L.append(f"| {tag} | {sig} | {m['n']} | {m['accuracy']['val']} | {m['ece']['val']} | "
                     f"{m['brier']['val']} | {au['val']} {au['ci']} |")
    gap_g = _paired_auroc_gap(gpt); gap_n = _paired_auroc_gap(main_)
    L += ["", "## Key question: does verbalized < self-consistency (AUROC) still hold?", "",
          f"- **gpt-oss-20b:** AUROC(self_consistency) − AUROC(verbalized) median = "
          f"{gap_g['median_gap'] if gap_g else 'n/a'}, 95% CI {gap_g['ci'] if gap_g else 'n/a'}, "
          f"P(gap>0) = {gap_g['p_gap_gt_0'] if gap_g else 'n/a'}",
          f"- **Nemotron (main, same 12):** median = {gap_n['median_gap'] if gap_n else 'n/a'}, "
          f"95% CI {gap_n['ci'] if gap_n else 'n/a'}, P(gap>0) = {gap_n['p_gap_gt_0'] if gap_n else 'n/a'}",
          "", "A positive gap (CI excluding 0) = self-consistency separates correct from wrong better "
          "than verbalized confidence — the main study's finding. This table states whether it replicates "
          "under a different extraction model.", ""]
    (out / "MODEL2.md").write_text("\n".join(L), encoding="utf-8")
    (out / "model2_metrics.json").write_text(json.dumps(
        {"label": LABEL, "selected_12": SEL, "n_gpt": len(gpt), "n_main": len(main_),
         "acc_abc": {"gpt": _acc_abc(gpt), "main": _acc_abc(main_)},
         "signals": {tag: {sig: _metrics(recs, sig) for sig in SIGNALS}
                     for tag, recs in (("gpt", gpt), ("main", main_))},
         "auroc_gap": {"gpt": gap_g, "main": gap_n}}, indent=2), encoding="utf-8")
    print(f"[model2] wrote {out/'MODEL2.md'} (gpt n={len(gpt)}, main n={len(main_)})")
    if gap_g:
        print(f"[model2] gpt-oss AUROC gap (SC-verb) median={gap_g['median_gap']} CI={gap_g['ci']}")


if __name__ == "__main__":
    main()
