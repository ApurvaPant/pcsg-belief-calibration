# LABEL_SENSITIVITY — run `main_20261015_000553` (offline, read-only)

No API calls, no pipeline changes, the running queue was not touched. Reconstructed
from the run DB (read-only) + `truth.json` + datagen splits by
`scripts/analysis/label_sensitivity.py`. Outputs: this file,
`results/analysis/label_audit.csv`, `results/analysis/label_audit_key.csv`.

## ⚠️ Data-integrity finding (read this first — it changes the run's T2 numbers)
**datagen(seed=2026, n=60) gives only 28 unique names for 60 personas** (17 names repeat
across *different* splits, e.g. `raj` = pid 0/fit **and** pid 53/dev; `ken` = pid 1/fit,
24/test, 50/dev). The run's calibration collector (`run._collect_signals`, and
`labelling` via `name_to_pid.setdefault`) attributes each assertion to a persona **by
name**, so all same-named personas **collapse onto one (lowest) pid**. Consequences in
this run:
- `labelled.jsonl` contains only **28 distinct personas**, not 60, and its split is the
  collision artifact **fit 862 / test 268 / dev 19**.
- Collapsed assertions are **labelled against the wrong persona's truth table**, which
  deflates accuracy. The previously reported T2 (test accuracy ≈ 0.556, verbalized/evidence
  AUROC ≈ 0.47) is **an artifact of this collision**, not the model's real behaviour.

Each DB user is a unique persona (replay creates one user per persona in pid order;
verified `user[i].label == personas[i].name` for **60/60**), so this analysis maps
assertions to personas by **user**, not name — the correct attribution.

**Fix for the pipeline (not applied here — analysis only):** make persona names unique
(e.g. append the pid) or key `_collect_signals`/labelling on a stable persona/user id
instead of the name, then re-run calibration.

## Q1 — split counts
| | fit | test | dev | total |
|---|---|---|---|---|
| **personas** (true, datagen) | 24 | 24 | 12 | 60 |
| **assertions** (corrected, per-user) | 459 | 459 | 231 | 1,149 |
| assertions (run's name-collapse = what T2 used) | 862 | 268 | 19 | 1,149 |

All metrics below use the **corrected** test set (n=459).

## Q3 — alternative label definitions (test set, n=459)
- **A** current: match iff a truth fact has the same `dimension` **and** normalised value.
- **B**: normalised value match, **dimension ignored**.
- **C**: B **or** fuzzy value (token-overlap ≥ 0.5 **or** substring containment, after normalisation).
- **D**: C **or** key-synonym match with fuzzy value. Explicit key-synonym map used:
  `diet↔food_preference, food_likes↔likes, occupation↔job, location↔city, music_genre↔music`.

| def | test acc | base-rate Brier | verbalized raw ECE/Brier/AUROC | verbalized Platt ECE/Brier/AUROC | self-cons raw ECE/Brier/AUROC | self-cons Platt ECE/Brier/AUROC |
|---|---|---|---|---|---|---|
| A | 0.8954 | 0.0936 | 0.055 / 0.1033 / **0.4487** | 0.0188 / 0.0937 / 0.5513 | 0.0777 / 0.0922 / **0.6802** | 0.0224 / 0.0843 / 0.6802 |
| B | 0.9041 | 0.0867 | 0.0637 / 0.0972 / 0.4189 | 0.0111 / 0.0865 / 0.5811 | 0.0777 / 0.0893 / 0.6757 | 0.0195 / 0.0808 / 0.6757 |
| C | 0.9346 | 0.0611 | 0.0637 / 0.0724 / 0.4652 | 0.0133 / 0.0614 / 0.5348 | 0.0821 / 0.0763 / 0.6409 | 0.0211 / 0.0591 / 0.6409 |
| D | 0.9346 | 0.0611 | (identical to C) | | | |

Takeaways: corrected accuracy is **0.90–0.93** (not ~0.55). **self_consistency is the
informative signal** (AUROC ≈ 0.64–0.68 across all definitions); **verbalized is near-chance**
(raw AUROC 0.42–0.47; Platt lifts it only to ~0.53–0.58). Loosening the label (A→C) raises
accuracy (0.895→0.935) and leaves AUROC ordering unchanged, so the signal conclusions are
**robust to the label definition**. **D == C** (the key-synonym map produced no extra matches
on this data).

## Q4 — label flips vs A
| comparison | flips / 459 | note |
|---|---|---|
| A → B | 4 | dimension-agnostic value match |
| A → C | 18 | fuzzy value match |
| A → D | 18 | key synonyms add 0 beyond C |

10 example A→C flips (A=0 → C=1 unless noted): `goal/career=manager` (truth has no goal);
`skill` and `interest` values that fuzzily overlap a truth value; `preference` values
contained in a truth value. (Full list + the exact matched truth in
`results/analysis/_digest.json → flips`.)

## Q5 — stratified accuracy / AUROC (verbalized), corrected test set
`END` and `REFINE` **do not occur in the test set** (n=0); only `ADD` and `UPDATE` appear.

**By operation**
| op | A: n / acc / AUROC | C: n / acc / AUROC |
|---|---|---|
| ADD | 411 / 0.883 / 0.419 | 411 / 0.927 / 0.437 |
| UPDATE | 48 / 1.000 / n/a (1 class) | 48 / 1.000 / n/a (1 class) |

**By dimension (A ; C)**
| dim | n | A acc / AUROC | C acc / AUROC |
|---|---|---|---|
| belief | 24 | 0.292 / 0.765 | 0.292 / 0.765 |
| goal | 22 | 0.273 / 0.500 | 0.727 / 0.500 |
| habit | 44 | 1.000 / n/a | 1.000 / n/a |
| identity | 146 | 0.945 / 0.261 | 0.966 / 0.202 |
| interest | 27 | 0.889 / 0.417 | 1.000 / n/a |
| preference | 50 | 1.000 / n/a | 1.000 / n/a |
| project | 24 | 1.000 / n/a | 1.000 / n/a |
| relationship | 69 | 1.000 / n/a | 1.000 / n/a |
| skill | 53 | 0.925 / 0.735 | 0.962 / 0.843 |

Notes: `belief` and `goal` are the low-accuracy dimensions (beliefs/goals extracted but
absent from the truth table → labelled spurious). Several dimensions are single-class at
1.0 accuracy, so AUROC is undefined there. `skill` shows the clearest signal discrimination
(AUROC 0.74→0.84 under C).

## Q2 — audit export
- `results/analysis/label_audit.csv` — **78 rows** = all **48** corrected-test assertions
  labelled 0 under A + **30** random test assertions labelled 1, **shuffled**; columns
  `id, persona_id, source_message, evidence_quote, subject, dimension, key, value,
  operation, truth_facts, human_label(empty)`. (The expected "119 zeros" was the
  name-collision count; corrected, there are 48 true label-0 test assertions.)
- `results/analysis/label_audit_key.csv` — hidden key: `id, label_A, A, B, C, D`.
`subject` is best-effort (`self`/`entity`); it is not stored per-assertion, so a human should
rely on `source_message` + extracted fields + `truth_facts`.
