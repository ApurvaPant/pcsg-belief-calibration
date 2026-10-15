# T2_corrected — corrected calibration, run `main_20261015_000553` (offline, read-only)

Per-**user** persona attribution (fixes the name-collision bug — see
`CORRECTED_RESULTS.md`), corrected test split **n=459**, fit split **n=459**.
No API calls; no pipeline files edited. Produced by `scripts/analysis/t2_corrected.py`.
`evidence` is omitted: it is a monotone transform of `verbalized` (ρ=1), so its
rank metrics equal verbalized's.

## Label definitions
- **A** current — same dimension + normalised value.
- **C** — value match (dimension-agnostic) + fuzzy (token-overlap ≥0.5 / containment).
- **E** — `normE` (lowercase; `_`/`-`→space; collapse; strip leading phrases
  *become a / learn to / want to / i / my / to*; lemmatise *coding→code, sketching→sketch, +ing→stem*);
  match on equal / containment / token-overlap ≥0.5 / number-in-range-or-decade
  (34∈30-39, 40∈40s). Two labels: **fact_correct** (any dimension) and **fully_correct**
  (same dimension, with skill/habit/interest treated as mutually compatible). The
  table uses **E = fully_correct**; **E fact_correct accuracy = 0.9935**.

## Accuracy and error count (test, n=459)
| def | accuracy | # wrong |
|---|---|---|
| A | 0.8954 | 48 |
| C | 0.9346 | 30 |
| E (fully_correct) | **0.9891** | **5** |
| E fact_correct | 0.9935 | 3 |

Corrected accuracy is **0.90–0.99**, not the ~0.55 the name-collision produced.

## Calibration metrics (test n=459; AUROC 95% CI = persona-clustered bootstrap, 1,000 resamples)
### verbalized
| def | ECE(10) | ECE(15q) | MCE(n≥10) | Brier | base-Brier | BSS | NLL | AUROC [95% CI] | Platt Brier / BSS / AUROC |
|---|---|---|---|---|---|---|---|---|---|
| A | 0.055 | 0.055 | 0.300 | 0.1033 | 0.0936 | −0.103 | 0.368 | 0.449 [0.398, 0.494] | 0.0937 / −0.001 / 0.551 |
| C | 0.064 | 0.072 | 0.300 | 0.0724 | 0.0611 | −0.185 | 0.282 | 0.465 [0.398, 0.542] | 0.0614 / −0.005 / 0.535 |
| E | 0.118 | 0.118 | 0.300 | 0.0303 | 0.0108 | −1.815 | 0.168 | 0.341 [0.063, 0.747] | 0.0107 / +0.004 / 0.659 |

### self_consistency
| def | ECE(10) | ECE(15q) | MCE(n≥10) | Brier | base-Brier | BSS | NLL | AUROC [95% CI] | Platt Brier / BSS / AUROC |
|---|---|---|---|---|---|---|---|---|---|
| A | 0.078 | 0.072 | 0.321 | 0.0922 | 0.0936 | +0.015 | 0.881 | **0.680 [0.620, 0.742]** | 0.0843 / +0.100 / 0.680 |
| C | 0.082 | 0.055 | 0.475 | 0.0763 | 0.0611 | −0.248 | 0.668 | **0.641 [0.568, 0.717]** | 0.0591 / +0.032 / 0.641 |
| E | 0.062 | 0.058 | 0.590 | 0.0378 | 0.0108 | −2.505 | 0.154 | **0.856 [0.721, 0.975]** | 0.0099 / +0.081 / 0.856 |

Isotonic recalibration (self_consistency): BSS +0.012 (A), −0.009 (C), **+0.091 (E)**.

### Reading these numbers
- **self_consistency is the only informative signal** (AUROC 0.64–0.86, CI excludes 0.5
  under A/C/E); **verbalized is ≈ chance** (AUROC 0.34–0.55, CIs straddle 0.5).
- **Raw signals are over-confident** → raw Brier worse than base-rate (negative BSS),
  because confidences sit at ~0.7–0.95 while errors are rare. **Platt fixes the magnitude**
  (Platt Brier ≈ base-rate; self_consistency Platt BSS positive) while leaving AUROC
  unchanged (Platt is monotone).
- Under **E** errors are so rare (5/459) that base-rate Brier is tiny (0.011) and the
  AUROC CI for verbalized is very wide [0.06, 0.75] — **the test split barely has enough
  errors to calibrate against**; self_consistency still separates them (AUROC 0.856).

## Accuracy under E by dimension / operation (test)
| dimension | n | acc(E) |   | operation | n | acc(E) |
|---|---|---|---|---|---|---|
| identity | 146 | 0.9795 | | ADD | 411 | 0.9878 |
| relationship | 69 | 1.000 | | UPDATE | 48 | 1.000 |
| skill | 53 | 0.9623 | | *(END/REFINE absent in test)* | 0 | — |
| preference | 50 | 1.000 | | | | |
| habit | 44 | 1.000 | | | | |
| interest | 27 | 1.000 | | | | |
| belief | 24 | 1.000 | | | | |
| project | 24 | 1.000 | | | | |
| goal | 22 | 1.000 | | | | |

Under the lenient E label, all residual errors are in `identity` (3) and `skill` (2);
`belief`/`goal` (which looked bad under A: 0.29/0.27) were label-definition artifacts,
not extraction errors.

## Figures
`results/analysis/F1_corrected_verbalized.{png,csv}` and `F1_corrected_self_consistency.{png,csv}`
— reliability diagrams (raw vs Platt, 10 bins) under label E.
