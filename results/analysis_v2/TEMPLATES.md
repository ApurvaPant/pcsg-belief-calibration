# TEMPLATES — duplicated / templated messages (analysis_v2)

OFFLINE, read-only. See the linkage note in the script header.

## Message duplication (normalized: lowercase, strip punctuation, collapse whitespace)

| split | total msgs | unique msgs | duplication rate |
|---|---|---|---|
| fit | 600 | 183 | 0.695 |
| test | 600 | 189 | 0.685 |
| dev | 300 | 140 | 0.533 |
| ALL | 1500 | 222 | 0.852 |

## Cross-persona templated evidence
- test beliefs (created/reinforced): 411
- **test beliefs whose evidence message text also appears for another persona: 405** (98.5%)

## Signal calibration under dedup (test split; text-linked subset)
- text-linked test beliefs: 313 of 459 (68% coverage; 475/535 (persona,dim) groups cleanly alignable)

| signal | set | n | acc | Brier | AUROC |
|---|---|---|---|---|---|
| verbalized | text-linked all | 313 | 0.872 | 0.126 | 0.372 |
| verbalized | unique-evidence (a) | 14 | 0.857 | 0.126 | 0.375 |
| self_consistency | text-linked all | 313 | 0.872 | 0.122 | 0.618 |
| self_consistency | unique-evidence (a) | 14 | 0.857 | 0.103 | 0.750 |

### (b) cluster bootstrap (cluster = normalized evidence template, 2000 reps, seed 2026)
| signal | AUROC [95% CI] | Brier [95% CI] |
|---|---|---|
| verbalized | 0.372 [0.231, 0.525] | 0.126 [0.054, 0.200] |
| self_consistency | 0.618 [0.506, 0.721] | 0.122 [0.050, 0.200] |

### Original (full test, no dedup) for comparison
| signal | n | acc | Brier | AUROC |
|---|---|---|---|---|
| verbalized | 459 | 0.895 | 0.103 | 0.449 |
| self_consistency | 459 | 0.895 | 0.092 | 0.680 |
