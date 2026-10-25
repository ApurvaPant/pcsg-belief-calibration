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
| original `evidence` (from extractor explicitness) | 0.3718 | 0.1212 |
| **post-hoc hedge-lexicon evidence** | 0.3581 | 0.1217 |
| verbalized (reference) | 0.3718 | 0.1256 |

Interpretation: this tests whether a transparent, reproducible hedge rule recovers (or beats) the extractor's own explicitness-based evidence signal. It is reported as post-hoc only.
