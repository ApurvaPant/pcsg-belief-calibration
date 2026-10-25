# TEMPLATES — duplicated / templated messages (analysis_v2)

OFFLINE, read-only. See the linkage note in the script header.

## Message duplication (normalized: lowercase, strip punctuation, collapse whitespace)

| split | total msgs | unique msgs | duplication rate |
|---|---|---|---|
| fit | 600 | 183 | 0.695 |
| test | 600 | 189 | 0.685 |
| dev | 300 | 140 | 0.5333 |
| ALL | 1500 | 222 | 0.852 |

## Cross-persona templated evidence
- test beliefs (created/reinforced): 411
- **test beliefs whose evidence message text also appears for another persona: 405** (98.5%)

## Signal calibration under dedup (test split; text-linked subset)
- text-linked test beliefs: 313 of 459 (68% coverage; 475/535 (persona,dim) groups cleanly alignable)

| signal | set | n | acc | Brier | AUROC |
|---|---|---|---|---|---|
| verbalized | text-linked all | 313 | 0.8722 | 0.1256 | 0.3718 |
| verbalized | unique-evidence (a) | 14 | 0.8571 | 0.1261 | 0.375 |
| self_consistency | text-linked all | 313 | 0.8722 | 0.1221 | 0.6184 |
| self_consistency | unique-evidence (a) | 14 | 0.8571 | 0.1032 | 0.75 |

### (b) cluster bootstrap (cluster = normalized evidence template, 2000 reps, seed 2026)
| signal | AUROC [95% CI] | Brier [95% CI] |
|---|---|---|
| verbalized | 0.3718 [0.2310, 0.5247] | 0.1256 [0.0543, 0.1999] |
| self_consistency | 0.6184 [0.5061, 0.7209] | 0.1221 [0.0503, 0.2001] |

### Original (full test, no dedup) for comparison
| signal | n | acc | Brier | AUROC |
|---|---|---|---|---|
| verbalized | 459 | 0.8954 | 0.1033 | 0.4487 |
| self_consistency | 459 | 0.8954 | 0.0922 | 0.6802 |
