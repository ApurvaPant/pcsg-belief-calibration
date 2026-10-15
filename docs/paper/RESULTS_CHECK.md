# RESULTS_CHECK — read-only audit of run `main_20261015_000553`

Provider **nvidia**, mode **live**, pipeline model **nvidia/nemotron-3-super-120b-a12b**,
judge **openai/gpt-oss-20b** (`results/runs/main_20261015_000553/config.json`). No code
changed, no API calls, the running queue was not touched. Evidence = file:line + counts
from `labelled.jsonl` (1,149 records), `calibration.json`, and the run DB (read-only).

## Q1 — verbalized and evidence both have raw AUROC 0.4737: is evidence independent?
**No. `evidence` is a deterministic, strictly-monotone transform of `verbalized`, so it carries no information beyond it.**

Code (`backend/app/signals.py:33-35`):
```python
def evidence_signal(verbalized: float, explicitness: str) -> float:
    prior = _EXPLICIT_PRIOR.get(explicitness, 0.5)        # _EXPLICIT_PRIOR = {"explicit":0.70,"implicit":0.40}  (signals.py:25)
    return noisy_or(float(verbalized), prior)
```
`noisy_or` (`backend/app/update.py:97-99`): `1 - (1-a)(1-b)`. With a fixed prior `b`, this is
`evidence = 1 - (1-verbalized)(1-b)` — **affine and monotincreasing in `verbalized`**. Since every
assertion in this run is `explicitness="explicit"` (Q3), `b=0.70` for all, so `evidence = 1 - 0.30·(1 - verbalized) = 0.30 + 0.70·verbalized`.

Measured on the 268 test assertions:
- **Pearson(verbalized, evidence) = 1.0000, Spearman = 1.0000.**
- AUROC is rank-based; a monotone transform preserves ranks, so the two signals have the **identical** AUROC (0.4737) **by construction**. They are not two independent signals — `evidence` adds nothing over `verbalized` here.

## Q2 — 1,149 labelled assertions but T2 test n=268: what's in the calibration test set?
Split is **by persona** (`run._replay` → `split_by_persona`); labels are **binary** `correct∈{0,1}` from
`labelling.label_one` (`pcsg/analysis/labelling.py:21-26`): `correct=1` iff some truth fact shares the same
`dimension` + normalised `value` (equivalent keys accepted, key ignored); otherwise `correct=0` — i.e. **`0` = "no matching truth fact" = unmatched/spurious**. There is no separate "matched-but-wrong" class and **nothing is excluded**.

`calibration.study` (`pcsg/analysis/calibration.py:119-128`) uses **`split=='test'` only** for the test set and
**`split=='fit'`** to fit recalibrators; the **dev split is not used** in T2. Per signal it keeps records whose
value is non-null.

| split | count | | label (test) | count |
|---|---|---|---|---|
| fit  | **862** | | correct (1) | **149** |
| test | **268** | | incorrect / unmatched (0) | **119** |
| dev  | **19** (unused by T2) | | total test | **268** |

Total 1,149 = 862+268+19. The calibration **test set = all 268 test-split assertions** (both labels). All three
signals are non-null on all 268 (verbalized/self_consistency/evidence each n=268, 0 nulls), so n=268 for every T2 row.

## Q3 — evidence ≈ 0.96 for almost all test assertions: explicitness + evidence counts
- **Explicitness is constant.** `explicitness` distribution is **`{explicit: 1149}`** across all labelled
  assertions (and 268/268 on test); DB `confidence_signals` agrees: **explicit 1149, implicit 0**. The extractor
  emits **only `explicit`** — there are **no `implicit` (nor any "implied"/"hedged") classes at all**. So the
  explicitness classifier is **not discriminating**; the evidence prior is always 0.70.
- Because `evidence = 0.30 + 0.70·verbalized` and verbalized ∈ {0.70,0.80,0.85,0.90,0.95} (mostly 0.90),
  evidence clusters high: test distribution **0.91×33, 0.94×4, 0.95×66, 0.97×127, 0.98×38** (std 0.0213).
- **Evidence quotes per assertion** (DB `evidence` table, per node): n_nodes=1,079, **min 1, max 2, mean 1.06**
  (1,009 nodes with 1 quote, 70 with 2). So each assertion is backed by ~1 quote; the "evidence" signal is not an
  evidence-*count* at all — it is the Noisy-OR of verbalized confidence with the (constant) explicitness prior.

**Verdict: the explicitness classifier is effectively inert (always "explicit"), which makes the evidence signal a
near-constant monotone function of verbalized — it is not an independent evidence measure in this run.**

## Q4 — provenance of `results/tables/T4.md`
**Not from this run.** `make_figures` builds T4 from `results/consistency/*/metamorphic.json`
(`scripts/make_figures.py` T4 block), and the only such file is **`results/consistency/20261009_121123/metamorphic.json`** —
a **2026-10-09** metamorphic (paraphrase-consistency) artefact from the earlier rc2 testing phase. That file records
**no provider / model / run_id**. So T4 reflects a *different, older* run (mock/real from 2026-10-09), **not** the
live NVIDIA run `main_20261015_000553`. T1/T2/T3/T5 come from this run; **T4 is stale** and should be regenerated (or
labelled with its true source) before it sits beside the others in the paper.

## Q5 — base-rate Brier vs raw, and whether recalibration is effectively constant
Test accuracy = **0.5560** for all signals (same 268 rows) → **base-rate Brier = acc·(1−acc) = 0.2469**.

| signal | raw AUROC | raw Brier | base-rate Brier | recalibrated Brier (temp/platt/iso) | recalibrated spread |
|---|---|---|---|---|---|
| verbalized | 0.4737 | 0.3566 | 0.2469 | 0.248 / 0.248 / 0.246 | std 0.007 / 0.001 / 0.050; 5/5/2 unique; range ≈ [0.51, 0.64] |
| self_consistency | 0.5537 | 0.3947 | 0.2469 | 0.248 / 0.241 / 0.241 | std 0.061 / 0.079 / 0.079; 4 unique |
| evidence | 0.4737 | 0.4131 | 0.2469 | 0.248 / 0.248 / 0.246 | std 0.006 / 0.001 / 0.050; 5/5/2 unique; range ≈ [0.51, 0.64] |

- **Raw Brier is worse than base-rate for every signal** (0.357–0.413 vs 0.247): the raw confidences (≈0.7–0.98)
  are badly overconfident relative to 55.6% accuracy (raw ECE 0.32–0.40).
- **Recalibrated predictions are effectively constant near the base rate.** verbalized/evidence collapse to
  ~0.52 (platt std 0.001, temperature std 0.007; isotonic → 2 values); recalibrated Brier ≈ 0.246–0.248 ≈ the
  base-rate 0.2469. self_consistency has a little spread (std 0.06–0.08, 4 unique) and is the only signal with
  AUROC above chance (0.554). In short, recalibration learns "predict the base rate" because the signals carry
  almost no discriminative information (AUROC ≈ 0.47–0.55 ≈ chance), and verbalized≡evidence by Q1.

## Bottom line
On this live run the confidence signals are essentially uninformative: verbalized confidence is near-constant
(mostly 0.9), explicitness is constant "explicit" so **evidence is a deterministic monotone copy of verbalized**
(ρ=1.0, identical AUROC 0.4737), AUROCs sit near chance, raw Brier is worse than base-rate, and recalibration
collapses to the base rate. The T2 test set is the 268 test-split assertions (149 correct / 119 unmatched),
with the 19 dev personas unused. T4 is from an older 2026-10-09 run, not this one.
