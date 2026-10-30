# HUMAN_AUDIT — analysis_v2 (annotator: raj)

OFFLINE, read-only. 78 audited beliefs. Signals matched by (persona,dimension,value) to persona_run/labelled (run DB gone); signal match coverage: 61/78.

Human label distribution: {'correct': 50, 'partly': 26, 'wrong': 2}

## 1. AUROC of signals vs human label (matched subset)

| signal | lenient AUROC (correct|partly=1) | strict AUROC (correct=1) |
|---|---|---|
| verbalized | 0.280 | 0.551 |
| self_consistency | 0.364 | 0.661 |

## 2. Rule vs human agreement (human lenient: correct|partly = correct)

| rule | % agreement with human (lenient) | % agreement (strict) |
|---|---|---|
| rule A | 0.385 | 0.615 |
| rule C | 0.590 | 0.436 |
| rule E | 0.910 | 0.705 |

## 3. Human-human Cohen's kappa
- **SKIPPED: no second-annotator file (error_audit_v2_apurva.csv absent); only raj annotated.**
