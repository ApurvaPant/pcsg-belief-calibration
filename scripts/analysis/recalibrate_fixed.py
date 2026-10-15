"""Step 4 (offline, read-only DB): re-run the calibration stage for this run with the
FIXED persona-id attribution (persona_users.json), verify 60 personas / 24-24-12 /
test n=459, and regenerate T2 + F1. Writes to results/runs/<run>_fixed/ and
results/analysis/ ONLY — never the live run dir (queue is running). No API calls.

  python scripts/analysis/recalibrate_fixed.py <run_id>
"""
from __future__ import annotations
import csv, json, os, sqlite3, tempfile, sys, pathlib, collections
REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))
from pcsg import datagen
from pcsg.analysis import labelling, calibration as C

RUN = sys.argv[1] if len(sys.argv) > 1 else "main_20261015_000553"
RUND = REPO / "results" / "runs" / RUN
FIXED = REPO / "results" / "runs" / f"{RUN}_fixed"; FIXED.mkdir(parents=True, exist_ok=True)
AOUT = REPO / "results" / "analysis"; AOUT.mkdir(parents=True, exist_ok=True)
DB = os.path.join(tempfile.gettempdir(), f"pcsg_exp_{RUN}.db")
cfg = json.loads((RUND / "config.json").read_text())
SEED, N = int(cfg.get("seed", 2026)), int(cfg.get("n_personas", 60))
truth = {int(k): v for k, v in json.loads((RUND / "truth.json").read_text()).items()}
personas = datagen.generate_personas(SEED, N)
split_by = {p.pid: p.split for p in personas}
uid2pid = {v: int(k) for k, v in json.loads((RUND / "persona_users.json").read_text()).items()}

# Reconstruct stored-by-persona with the FIXED id mapping (read-only DB).
c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True); c.row_factory = sqlite3.Row
rows = c.execute("SELECT cs.dimension,cs.key,cs.value,cs.verbalized,cs.self_consistency,cs.evidence,"
                 "cs.explicitness,n.user_id AS uid FROM confidence_signals cs JOIN nodes n ON n.id=cs.node_id").fetchall()
stored = {p.pid: [] for p in personas}
for r in rows:
    pid = uid2pid.get(r["uid"])
    if pid is None:
        continue
    stored[pid].append({"dimension": r["dimension"], "key": r["key"], "value": r["value"],
                        "verbalized": r["verbalized"], "self_consistency": r["self_consistency"],
                        "evidence": r["evidence"], "explicitness": r["explicitness"]})

records = labelling.label_run(stored, truth, split_by)   # A default + C/E side by side

# ── verification ─────────────────────────────────────────────────────────── #
pers_split = collections.Counter(split_by.values())
asrt_split = collections.Counter(r["split"] for r in records)
n_personas_with_data = sum(1 for pid in stored if stored[pid])
n_test = asrt_split["test"]
print(f"personas: {len(personas)} (with data: {n_personas_with_data})  persona split: {dict(pers_split)}")
print(f"assertion split: {dict(asrt_split)}  | test assertions n={n_test}")
checks = {"60 personas": len(personas) == 60, "all 60 have data": n_personas_with_data == 60,
          "persona split 24/24/12": dict(pers_split) == {"fit": 24, "test": 24, "dev": 12},
          "test n==459": n_test == 459}
print("VERIFY:", checks, "ALL_PASS=", all(checks.values()))

# ── corrected calibration.json (default label A) ────────────────────────────── #
cal = C.study(records)
cal["watermark"] = None
cal["label_definition"] = "A (default); C/E available in labelled.jsonl"
(FIXED / "calibration.json").write_text(json.dumps(cal, indent=2), encoding="utf-8")
with open(FIXED / "labelled.jsonl", "w", encoding="utf-8") as fh:
    for r in records:
        fh.write(json.dumps(r) + "\n")
# copy config (mode=live) so make_figures --require-live can consume _fixed post-queue
cfg2 = dict(cfg); cfg2["run_id"] = f"{RUN}_fixed"; cfg2["note"] = "offline recalibration, fixed persona-id attribution"
(FIXED / "config.json").write_text(json.dumps(cfg2, indent=2), encoding="utf-8")

# ── T2 + F1 (regenerated) into results/analysis/ ────────────────────────────── #
try:
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    HAVE = True
except Exception:
    HAVE = False
lines = [f"# T2 corrected (fixed persona-id attribution) - run {RUN}", "",
         f"n_fit={cal['n_fit']} n_test={cal['n_test']} | personas 60 (24/24/12) | label A (default)", "",
         "| signal | recalibrator | ECE(10) | ECE(15q) | MCE | Brier | NLL | AUROC |",
         "|---|---|---|---|---|---|---|---|"]
for sig, recs in cal["by_signal_recalibrator"].items():
    for rec, m in recs.items():
        lines.append(f"| {sig} | {rec} | {m['ece']} | {m.get('ece_quantile')} | {m.get('mce')} | {m['brier']} | {m.get('nll')} | {m['auroc']} |")
(AOUT / "T2_fixed.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
for sig, s in cal.get("reliability", {}).items():
    with open(AOUT / f"F1_fixed_{sig}.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh); w.writerow(["stage", "conf", "acc", "n"])
        for stage in ("before", "after"):
            for b in s.get(stage, []):
                w.writerow([stage, b["conf"], b["acc"], b["n"]])
    if HAVE:
        plt.figure(figsize=(4, 4)); plt.plot([0, 1], [0, 1], "k--", lw=0.8, label="perfect")
        for stage, mk in (("before", "o"), ("after", "s")):
            pts = s.get(stage, [])
            if pts: plt.plot([p["conf"] for p in pts], [p["acc"] for p in pts], marker=mk, label=stage)
        plt.xlabel("confidence"); plt.ylabel("accuracy"); plt.title(f"F1 fixed - {sig} (A, raw vs recal)")
        plt.legend(); plt.xlim(0, 1); plt.ylim(0, 1)
        plt.savefig(AOUT / f"F1_fixed_{sig}.png", dpi=110, bbox_inches="tight"); plt.close()

print(f"wrote {FIXED}/calibration.json + labelled.jsonl; T2_fixed.md + F1_fixed_* in {AOUT}")
