# HUMAN_AUDIT - run main_20261015_000553 (annotator: raj)

78 corrected-test audit rows (48 rule-A-wrong + 30 rule-A-correct). Human labels joined on id with
rule labels A/C/E (error_audit_v2_key.csv) and verbalized/self_consistency (run DB, read-only). N=78.
No second annotator file (error_audit_v2_ujjwal.csv absent) -> Cohen's kappa not computed.

Human label distribution: correct 50, partly 26, wrong 2.

## 1. Human label vs rule (confusion + % agreement)

**Rule A** (rows x rule label):

| human \ rule | rule=0 (wrong) | rule=1 (correct) |
|---|---|---|
| correct | 25 | 25 |
| partly | 22 | 4 |
| wrong | 1 | 1 |

agreement: strict (partly=wrong) **61.5%** ; lenient (partly=correct) **38.5%**

**Rule C** (rows x rule label):

| human \ rule | rule=0 (wrong) | rule=1 (correct) |
|---|---|---|
| correct | 23 | 27 |
| partly | 7 | 19 |
| wrong | 0 | 2 |

agreement: strict (partly=wrong) **43.6%** ; lenient (partly=correct) **59.0%**

**Rule E** (rows x rule label):

| human \ rule | rule=0 (wrong) | rule=1 (correct) |
|---|---|---|
| correct | 0 | 50 |
| partly | 5 | 21 |
| wrong | 0 | 2 |

agreement: strict (partly=wrong) **70.5%** ; lenient (partly=correct) **91.0%**

## 2. Per human label: confidence stats

| human | n | mean verbalized | mean self_consistency | share self_consistency<1 |
|---|---|---|---|---|
| correct | 50 | 0.883 | 0.933 | 7/50 = 14% |
| partly | 26 | 0.881 | 0.615 | 18/26 = 69% |
| wrong | 2 | 0.800 | 1.000 | 0/2 = 0% |

## 3. AUROC: separating human-correct from (partly + wrong), N=78, 1,000-resample bootstrap 95% CI

| signal | AUROC | 95% CI | n (non-null) |
|---|---|---|---|
| verbalized | 0.4579 | [0.3199, 0.5903] | 78 |
| self_consistency | 0.7593 | [0.6525, 0.8624] | 78 |

## 4. 'partly' note types

| note type | count |
|---|---|
| missing detail | 11 |
| age range | 9 |
| wrong category | 6 |

(total partly = 26)

