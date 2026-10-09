#!/usr/bin/env python
"""STEP 9 — regenerate every paper table/figure from results/ alone.

NO model calls. Pure stdlib; matplotlib is used only if importable (figures fall
back to CSV data + a note otherwise). Each artifact has a declared input file
under results/; when the input is missing (a paid step hasn't run yet) the script
writes a PENDING placeholder so the harness always runs and states what is absent.

Artifacts:
  T1 extraction (results/extraction_eval.json)      T2 calibration table (results/calibration.json)
  T3 baselines  (results/baselines.json)            T4 paraphrase consistency (results/consistency/*/metamorphic.json)
  F1 reliability diagrams (results/calibration.json) F2 abstention AURC (results/abstention.json)
  F3 accuracy-vs-cost (results/cost_curve.json)

Usage:  python scripts/make_figures.py
"""
from __future__ import annotations

import csv
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
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


def _load(path: pathlib.Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _watermark() -> str | None:
    """A visible watermark if any result was produced in dry-run (synthetic)."""
    for f in ("calibration.json", "abstention.json", "cost_curve.json"):
        d = _load(RES / f)
        if d and d.get("watermark"):
            return d["watermark"]
    return None


WM = _watermark()


def _banner(md: str) -> str:
    return (f"> ⚠️ **{WM}** — not real results.\n\n" + md) if WM else md


def _md_table(rows: list[list], header: list[str]) -> str:
    out = ["| " + " | ".join(header) + " |", "|" + "|".join(["---"] * len(header)) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(out)


def _pending(name: str, inp: str, why: str) -> str:
    return f"# {name}\n\n**PENDING** — input `{inp}` not found ({why}). Run the producing step, then re-run make_figures.py.\n"


def _latest_consistency() -> pathlib.Path | None:
    cands = sorted((RES / "consistency").glob("*/metamorphic.json")) if (RES / "consistency").exists() else []
    return cands[-1] if cands else None


def t1_extraction():
    d = _load(RES / "extraction_eval.json")
    if not d:
        return _pending("T1 — Extraction", "results/extraction_eval.json", "STEP 2 not run")
    rows = []
    for split in ("dev", "test"):
        s = d.get(split, {})
        rows.append([split, s.get("pass_rate"), s.get("precision"), s.get("recall"),
                     s.get("f1"), s.get("false_write_rate"), s.get("end_precision")])
    return "# T1 — Extraction\n\n" + _md_table(
        rows, ["split", "pass", "P", "R", "F1", "false-write", "END-prec"])


def t4_consistency():
    path = _latest_consistency()
    d = _load(path) if path else None
    if not d:
        return _pending("T4 — Paraphrase consistency", "results/consistency/*/metamorphic.json", "STEP -1/metamorphic not run")
    strict = d.get("MT1", {})
    val = d.get("MT1_value_level", {})
    rows = [[fam, info.get("consistency"), val.get(fam, "—")] for fam, info in strict.items()]
    sm = [r[1] for r in rows if isinstance(r[1], (int, float))]
    vm = [v for v in val.values() if isinstance(v, (int, float))]
    foot = f"\n\nmean strict={sum(sm)/len(sm):.3f}  mean value-level={sum(vm)/len(vm):.3f}" if sm and vm else ""
    return "# T4 — Paraphrase consistency (MT-1)\n\n" + _md_table(
        rows, ["family", "strict", "value-level"]) + foot


def t2_calibration():
    d = _load(RES / "calibration.json")
    if not d:
        return _pending("T2 — Calibration", "results/calibration.json", "STEP 4 not run")
    rows = []
    for sig, recs in d.get("by_signal_recalibrator", {}).items():
        for rec, m in recs.items():
            rows.append([sig, rec, m.get("ece"), m.get("brier"), m.get("auroc"), m.get("ci")])
    return "# T2 — Calibration (signal x recalibrator)\n\n" + _md_table(
        rows, ["signal", "recalibrator", "ECE", "Brier", "AUROC", "95% CI"])


def t3_baselines():
    d = _load(RES / "baselines.json")
    if not d:
        return _pending("T3 — Baselines", "results/baselines.json", "STEP 8 not run")
    rows = [[b, m.get("accuracy"), m.get("fabrication_rate"), m.get("tokens_per_q")]
            for b, m in d.items()]
    return "# T3 — Baselines\n\n" + _md_table(rows, ["system", "accuracy", "fabrication", "tokens/Q"])


def f_generic(name, inp, why, plotter):
    d = _load(RES / inp)
    if not d:
        (FIG / f"{name}.md").write_text(_pending(name, f"results/{inp}", why), encoding="utf-8")
        return f"{name}: PENDING ({inp} missing)"
    # always emit the underlying data as CSV so the figure is reproducible w/o mpl
    csv_path = FIG / f"{name}.csv"
    plotter(d, csv_path)
    return f"{name}: data -> {csv_path.name}" + ("" if HAVE_MPL else "  (PNG skipped: matplotlib not installed)")


def _f1(d, csv_path):
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh); w.writerow(["signal", "stage", "bin", "confidence", "accuracy"])
        for sig, series in d.get("reliability", {}).items():
            for stage in ("before", "after"):
                for b in series.get(stage, []):
                    w.writerow([sig, stage, b.get("bin"), b.get("conf"), b.get("acc")])
    if HAVE_MPL:
        for sig, series in d.get("reliability", {}).items():
            plt.figure(figsize=(4, 4))
            for stage in ("before", "after"):
                pts = series.get(stage, [])
                if pts:
                    plt.plot([p["conf"] for p in pts], [p["acc"] for p in pts], marker="o", label=stage)
            plt.plot([0, 1], [0, 1], "k--", lw=0.8)
            plt.xlabel("confidence"); plt.ylabel("accuracy"); plt.title(f"F1 reliability — {sig}"); plt.legend()
            if WM:
                plt.figtext(0.5, 0.5, WM, fontsize=22, color="red", alpha=0.25,
                            ha="center", va="center", rotation=30, weight="bold")
            plt.savefig(FIG / f"F1_{sig}.png", dpi=120, bbox_inches="tight"); plt.close()


def _f2(d, csv_path):
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh); w.writerow(["signal", "tau", "coverage", "accuracy", "fabrication"])
        for sig, series in d.get("sweep", {}).items():
            for pt in series:
                w.writerow([sig, pt.get("tau"), pt.get("coverage"), pt.get("accuracy"), pt.get("fabrication")])


def _f3(d, csv_path):
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh); w.writerow(["cell", "tier1", "write_tau", "accuracy", "cost_tokens", "ece", "pareto"])
        for c in d.get("cells", []):
            w.writerow([c.get("cell"), c.get("tier1"), c.get("write_tau"), c.get("accuracy"),
                        c.get("cost_tokens"), c.get("ece"), c.get("pareto")])


def main():
    tables = {"T1": t1_extraction(), "T2": t2_calibration(), "T3": t3_baselines(), "T4": t4_consistency()}
    for name, md in tables.items():
        (TAB / f"{name}.md").write_text(_banner(md) + "\n", encoding="utf-8")
        print(f"table {name} -> results/tables/{name}.md" + ("  [WATERMARKED]" if WM else ""))
    for line in (
        f_generic("F1", "calibration.json", "STEP 4 not run", _f1),
        f_generic("F2", "abstention.json", "STEP 6 not run", _f2),
        f_generic("F3", "cost_curve.json", "STEP 7 not run", _f3),
    ):
        print(line)
    print(f"\nmatplotlib: {'available' if HAVE_MPL else 'NOT installed (CSV emitted; install to render PNGs)'}")
    print("Done — no model calls were made.")


if __name__ == "__main__":
    main()
