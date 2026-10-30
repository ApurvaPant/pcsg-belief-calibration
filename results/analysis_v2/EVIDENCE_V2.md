# EVIDENCE_V2 — post-hoc deterministic explicitness (analysis_v2)

**POST-HOC sensitivity analysis. `confidence_raw` is untouched.** OFFLINE, read-only.
Hedge lexicon: ['might', 'maybe', 'probably', 'perhaps', 'thinking about', 'considering', 'planning to', 'hoping to', 'kind of', 'sort of', 'i guess', 'not sure', 'possibly']
Priors (from config / signals._EXPLICIT_PRIOR): {'explicit': 0.7, 'implicit': 0.4}

Text-linked test beliefs: 313 of 459 (68% coverage). No 'implied' class was added (no simple deterministic rule that is testable; kept binary hedged->implicit / explicit).

## Class distribution (post-hoc vs original extractor explicitness)
- post-hoc: {'explicit': 289, 'implicit': 24}
- original: {'explicit': 313}

## AUROC / Brier vs rule-A correctness (test, text-linked subset)

| evidence signal | AUROC | Brier |
|---|---|---|
| original `evidence` (from extractor explicitness) | 0.372 | 0.121 |
| **post-hoc hedge-lexicon evidence** | 0.358 | 0.122 |
| verbalized (reference) | 0.372 | 0.126 |

Interpretation: this tests whether a transparent, reproducible hedge rule recovers (or beats) the extractor's own explicitness-based evidence signal. It is reported as post-hoc only.

## Canonical full-test original evidence signal (rule A, n=459 — NOT subset-restricted)
- original `evidence` AUROC = **0.449**, verbalized AUROC = **0.449**, Spearman rho(evidence, verbalized) = **1.000**; test explicitness = {'explicit': 459}.
- The paper's claim holds: on the FULL test split every belief is `explicit`, so evidence = noisy_or(verbalized, 0.70) is a strictly monotonic transform of verbalized -> IDENTICAL ranking -> AUROC 0.449 == verbalized 0.449 (~0.45), rho = 1.000. The 0.372 above is a sampling artifact of the 68%-coverage text-linked subset (n=313), not a different signal.
