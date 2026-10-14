#!/usr/bin/env python
"""STEP 9 / viz STEP 1 — regenerate every paper table/figure from results/ alone.

NO model calls. Builds results/<run_id>/dashboard.json (the single data contract)
from the latest run, copies it to results/dashboard.json, and renders T1-T4 / F1-F3
FROM dashboard.json so the paper figures and the Results tab can never disagree.
matplotlib is optional (figures fall back to CSV). Dry-run artefacts are watermarked.

Usage:  python scripts/make_figures.py [run_id]
"""
from __future__ import annotations

import csv
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))
RES = ROOT / "results"
TAB = RES / "tables"
FIG = RES / "figures"
TAB.mkdir(parents=True, exist_ok=True)
FIG.mkdir(parents=True, exist_ok=True)

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    HAVE_MPL = True
except Exception:  # noqa: BLE001
    HAVE_MPL = False


def _load(p):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _latest_run():
    runs = sorted((RES / "runs").glob("*/config.json")) if (RES / "runs").exists() else []
    return runs[-1].parent if runs else None


def _build_dashboard(run_id=None):
    from pcsg.viz.build import write_dashboard
    run_dir = (RES / "runs" / run_id) if run_id else _latest_run()
    if run_dir is None or not run_dir.exists():
        return None, [], None
    out, errs = write_dashboard(run_dir)
    (RES / "dashboard.json").write_text(out.read_text(encoding="utf-8"), encoding="utf-8")
    return _load(RES / "dashboard.json"), errs, run_dir


def _md(rows, header):
    return "\n".join(["| " + " | ".join(header) + " |", "|" + "|".join(["---"] * len(header)) + "|",
                      *["| " + " | ".join(str(c) for c in r) + " |" for r in rows]])


def _banner(md, wm):
    return (f"> ⚠️ **{wm}** — not real results.\n\n" + md) if wm else md


def _pending(name, why):
    return f"# {name}\n\n**PENDING** — {why}.\n"


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("run_id", nargs="?", default=None, help="build figures from this run id (default: latest)")
    ap.add_argument("--run-id", dest="run_id_opt", default=None, help="same as positional run id")
    ap.add_argument("--require-live", action="store_true",
                    help="refuse to build if the run's mode is dry-run (so figures can never be made from a dry run)")
    a = ap.parse_args()
    run_id = a.run_id_opt or a.run_id

    # Guard: resolve the run and refuse dry-run / mismatch when --require-live.
    run_dir = (RES / "runs" / run_id) if run_id else _latest_run()
    if run_dir is None or not run_dir.exists():
        print(f"make_figures: no run found ({run_id or 'latest'}) under results/runs/.")
        return 1
    cfg = _load(run_dir / "config.json") or {}
    cfg_mode, cfg_id = cfg.get("mode"), cfg.get("run_id", run_dir.name)
    if run_id and cfg_id != run_id:
        print(f"make_figures: GUARD FAIL — resolved run_id {cfg_id!r} != requested {run_id!r}.")
        return 2
    if a.require_live and cfg_mode != "live":
        print(f"make_figures: GUARD FAIL — run {cfg_id!r} mode is {cfg_mode!r}, not 'live'. "
              f"Refusing to build paper figures from a non-live run.")
        return 2

    dash, errs, _ = _build_dashboard(run_id)
    if dash is None:
        print("No run found under results/runs/.")
        return 1
    if errs:
        print("dashboard.json schema errors:", errs)
    wm = dash.get("watermark")
    print(f"make_figures: building from run_id={cfg_id} mode={cfg_mode}")

    # T2 calibration (from dashboard)
    rows = []
    for sig, s in dash.get("calibration", {}).get("signals", {}).items():
        for rec, m in s["recalibrators"].items():
            rows.append([sig, rec, m["ece"], m["brier"], m["auroc"], m.get("ci", {}).get("ece", "")])
    t2 = ("# T2 — Calibration\n\n" + _md(rows, ["signal", "recalibrator", "ECE", "Brier", "AUROC", "95% CI"])) \
        if rows else _pending("T2 — Calibration", "no calibration in dashboard")
    (TAB / "T2.md").write_text(_banner(t2, wm) + "\n", encoding="utf-8")

    # T3 baselines (from dashboard)
    bl = dash.get("baselines", [])
    t3 = ("# T3 — Baselines\n\n" + _md([[b["system"], b.get("accuracy"), b.get("fabrication"), b.get("tokens_per_q")] for b in bl],
                                       ["system", "accuracy", "fabrication", "tokens/Q"])) if bl \
        else _pending("T3 — Baselines", "STEP 8 baselines not run (live)")
    (TAB / "T3.md").write_text(_banner(t3, wm) + "\n", encoding="utf-8")

    # T1 extraction (own file; live)
    ee = _load(RES / "extraction_eval.json")
    t1 = _pending("T1 — Extraction", "STEP 2 extraction eval not run (live)") if not ee else \
        "# T1 — Extraction\n\n" + _md([[s, ee.get(s, {}).get("f1")] for s in ("dev", "test")], ["split", "F1"])
    (TAB / "T1.md").write_text(_banner(t1, wm) + "\n", encoding="utf-8")

    # T4 consistency (separate metamorphic artefact)
    cons = sorted((RES / "consistency").glob("*/metamorphic.json")) if (RES / "consistency").exists() else []
    if cons:
        d = _load(cons[-1])
        vl = d.get("MT1_value_level", {})
        t4 = "# T4 — Paraphrase consistency\n\n" + _md(
            [[f, i["consistency"], vl.get(f, "—")] for f, i in d["MT1"].items()], ["family", "strict", "value"])
    else:
        t4 = _pending("T4 — Paraphrase consistency", "metamorphic not run")
    (TAB / "T4.md").write_text(_banner(t4, wm) + "\n", encoding="utf-8")

    # T5 LongMemEval (Tier 3) — latest REAL run only (results/runs); dryrun stays
    # isolated and never mixes into the paper's final tables.
    lme_files = sorted((RES / "runs").glob("*/longmemeval.json"), key=lambda p: p.stat().st_mtime) \
        if (RES / "runs").exists() else []
    if lme_files:
        lme = _load(lme_files[-1]) or {}
        m = lme.get("metrics", {})
        rows = [[s, mm.get("accuracy_overall"), mm.get("abstention_accuracy"),
                 mm.get("answer_ece"), mm.get("tokens_per_q")] for s, mm in m.items()]
        t5 = (f"# T5 — LongMemEval (rev {str(lme.get('revision'))[:12]}, "
              f"n={lme.get('n_questions')}, types={lme.get('count_by_type')})\n\n" +
              _md(rows, ["system", "accuracy", "abstention_acc", "answer_ECE", "tokens/Q"]))
    else:
        t5 = _pending("T5 — LongMemEval", "LongMemEval runner not run (needs --lme / live)")
    (TAB / "T5.md").write_text(_banner(t5, wm) + "\n", encoding="utf-8")

    # F1 reliability (per signal) from dashboard
    for sig, s in dash.get("calibration", {}).get("signals", {}).items():
        with open(FIG / f"F1_{sig}.csv", "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh); w.writerow(["stage", "conf", "acc", "n"])
            for stage in ("before", "after"):
                for b in s["reliability"].get(stage, []):
                    w.writerow([stage, b["conf"], b["acc"], b["n"]])
        if HAVE_MPL:
            plt.figure(figsize=(4, 4))
            for stage in ("before", "after"):
                pts = s["reliability"].get(stage, [])
                if pts:
                    plt.plot([p["conf"] for p in pts], [p["acc"] for p in pts], marker="o", label=stage)
            plt.plot([0, 1], [0, 1], "k--", lw=0.8)
            plt.xlabel("confidence"); plt.ylabel("accuracy"); plt.title(f"F1 — {sig}"); plt.legend()
            if wm:
                plt.figtext(0.5, 0.5, wm, fontsize=16, color="red", alpha=0.25, ha="center", va="center", rotation=30)
            plt.savefig(FIG / f"F1_{sig}.png", dpi=110, bbox_inches="tight"); plt.close()

    # F2 abstention + F3 cost: emit CSV from dashboard
    abn = dash.get("abstention", {}).get("sweep", {})
    with open(FIG / "F2.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh); w.writerow(["variant", "tau", "coverage", "accuracy", "fabrication"])
        for variant, series in abn.items():
            for pt in series:
                w.writerow([variant, pt["tau"], pt["coverage"], pt["accuracy"], pt["fabrication"]])
    with open(FIG / "F3.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh); w.writerow(["cell", "tier1", "write_tau", "accuracy", "cost_tokens", "pareto"])
        for c in dash.get("cost_curve", {}).get("cells", []):
            w.writerow([c["cell"], c["tier1"], c["write_tau"], c["accuracy"], c["cost_tokens"], c.get("pareto")])

    print(f"dashboard: results/dashboard.json  (run {dash['meta']['run_id']}, mode={dash['meta']['mode']})")
    print(f"tables -> results/tables/  figures -> results/figures/  {'[WATERMARKED]' if wm else ''}")
    print(f"matplotlib: {'available' if HAVE_MPL else 'absent (CSV only)'}; no model calls.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
