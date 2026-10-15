# CORRECTED_RESULTS — run `main_20261015_000553` (offline, read-only)

Summary of the follow-up label audit. No API calls, no pipeline files edited (the
queue is still running); new files only under `scripts/analysis/` and
`results/analysis/`. Companion detail: `T2_corrected.md`, `LABEL_SENSITIVITY.md`,
`error_audit.csv`(+key), `F1_corrected_*.{png,csv}`.

## 1. Scope of the persona-name-collision bug
datagen(seed=2026, n=60) emits only **28 unique names for 60 personas**; code that
maps an assertion/question to a persona **by name** collapses the duplicates.

| component | identifies persona by… | verdict |
|---|---|---|
| Tier-1 calibration collector — `run._collect_signals` (`name_to_pid.setdefault`, `pcsg/run.py:209`) | **name** | **AFFECTED** — only 28/60 personas in `labelled.jsonl`; split 862/268/19; labels against wrong truth |
| Tier-2 QA — PCSG answerer memory source — `pcsg/run.py:240` `label_to_uid={label:id}`, `:246` `uid=label_to_uid.get(p.name)`, `:262` `answer_pcsg(...,uid)` | **name** (last user wins) | **AFFECTED** — **13/24 test personas, 143/264 PCSG questions (54%) answer from the WRONG persona's graph** |
| Tier-2 QA — `full_context` / `vector_rag` (`:247,260-261`, history from `p.turns`) | persona object | NOT affected |
| Tier-2 QA — `no_memory` | — | NOT affected |
| Tier-2 QA — question + gold + judge (`make_qa(p)` `:248`, `q["gold"]` `:250,270`) | persona object | NOT affected |
| LongMemEval runner — `pcsg/longmemeval_run.py:96` (single `"lme"` user, dataset `question_id`) | dataset id | NOT affected |

**Net:** Tier-1 calibration (T1/T2/F1) and the **PCSG column** of Tier-2 (T3/F2) are
invalid as produced; the three baseline systems, the judge, and all of LongMemEval
(T5) are fine. ~**54%** of PCSG QA answers were drawn from the wrong user's memory.

## 2. Corrected calibration (per-user attribution, test n=459)
The run's T2 (test acc ≈ 0.556, AUROC ≈ 0.47) was a collision artifact. With correct
per-persona truth attribution:

| label | accuracy | # wrong | verbalized AUROC [95% CI] | self_consistency AUROC [95% CI] |
|---|---|---|---|---|
| A (dim+value) | 0.895 | 48 | 0.449 [0.398, 0.494] | **0.680 [0.620, 0.742]** |
| C (fuzzy) | 0.935 | 30 | 0.465 [0.398, 0.542] | **0.641 [0.568, 0.717]** |
| E (fully_correct) | **0.989** | 5 | 0.341 [0.063, 0.747] | **0.856 [0.721, 0.975]** |
| E fact_correct (any dim) | 0.994 | 3 | — | — |

- **True extraction accuracy is 0.90–0.99** (label-dependent), not ~0.55.
- **self_consistency is the only informative confidence signal** (AUROC 0.64–0.86, CI
  excludes 0.5); **verbalized ≈ chance** (0.34–0.55, CIs straddle 0.5), and **evidence ≡
  verbalized** (monotone, ρ=1).
- Raw signals are **over-confident** (raw Brier > base-rate → negative BSS); **Platt**
  fixes magnitude (self_consistency Platt BSS +0.03…+0.10) without changing AUROC.
- Under E, errors are rare (5/459) so base-rate Brier is tiny (0.011) and verbalized's
  AUROC CI is very wide — the split barely has enough errors to calibrate. Residual E
  errors are only in `identity` (3) and `skill` (2); `belief`/`goal` looked bad under A
  (0.29/0.27) purely as a label-definition artifact. END/REFINE do not occur in the test split.

## 3. Audit files for human review
- `error_audit.csv` (UTF-8 **BOM**, 35 rows) — all **5** E-wrong test assertions + 30 random
  correct, shuffled; columns `id, persona_id, source_message, subject, dimension, key,
  value, operation, top3_truth_facts, human_label(empty)`. Hidden labels in `error_audit_key.csv`.
- `label_audit.csv` (from the first pass, 78 rows) remains for the broader A-label audit.

## 4. Exact pipeline fix — APPLY ONLY AFTER THE QUEUE FINISHES
Do **not** edit these now (later queue stages import them). Two coordinated changes:

1. **Make persona names unique (root cause).** In `backend/pcsg/datagen/__init__.py` where
   `Persona.name` is assigned, append the pid, e.g. `name = f"{base}-{i}"` (or `f"{base}#{pid}"`).
   This alone removes the collision for any name-keyed lookup. *Changes persona identity
   strings, so re-run from Tier 1.*

2. **Key persona↔user on a stable id, never the name.** During replay
   (`run._replay`, the `INSERT INTO users … p.name` loop) capture and persist a
   `pid → user_id` map (e.g. write `results/runs/<id>/persona_users.json`), then:
   - `run._collect_signals`: drop `name_to_pid`; map each `confidence_signals` row to a
     persona via `nodes.user_id → pid` using that map.
   - `run._qa_and_abstention`: replace `label_to_uid = {label:id}` / `uid = label_to_uid.get(p.name)`
     with `uid = pid2uid[p.pid]` from the persisted map.

3. **Re-run** Tier 1 (replay→calibration) and the Tier-2 **PCSG** QA/abstention for a fresh
   run id; T1/T2/F1 and the PCSG column of T3/F2 from `main_20261015_000553` must be
   discarded. Baselines, judge, and LongMemEval (T5) from this run remain valid.

Verification after the fix: `labelled.jsonl` should contain **60** distinct personas and a
~**24/24/12**-persona split (assertion split ≈ 459/459/231), and corrected test accuracy
(~0.90–0.99) should match this analysis.
