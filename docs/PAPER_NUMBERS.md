# PAPER_NUMBERS — provenance of every headline number

Each number → source file + field + the script that regenerates it. All reproduce
**offline** via `scripts/reproduce.ps1` / `.sh` unless flagged. Run folder:
`results/runs/main_20261015_000553/` (Nemotron 3 Super; read-only).

Legend: ✅ reproduces exactly · ⚠️ reproduces with caveat · ❌ does NOT reproduce from committed artifacts · 📊 aggregate-only (no per-item records).

## Tier-1 calibration (test split, n=459)
| paper number | value found | source → field | script | status |
|---|---|---|---|---|
| 89.5% correct | 0.8954 | labelled.jsonl → mean(correct_A) over split=test | sig_recalib.py / make_figures | ✅ |
| 48 errors / 459 | 48 / 459 | labelled.jsonl → sum(1-correct_A), test | sig_recalib.py | ✅ |
| AUROC 0.45 [0.40,0.49] (verbalized) | 0.4487 | calibration.json → by_signal_recalibrator.verbalized.raw.auroc | make_figures (T2); CI in STATS.md | ✅ (point); CI from bootstrap |
| AUROC 0.68 [0.62,0.74] (self-consistency) | 0.6802 | calibration.json → self_consistency.raw.auroc | make_figures (T2); STATS.md | ✅ (point); CI from bootstrap |
| Table II (ECE10/Brier/NLL/AUROC × raw/temp/Platt/isotonic) | — | calibration.json → by_signal_recalibrator | make_figures → results/tables/T2*.md | ✅ |
| base-rate Brier 0.094 | 0.0936 | labelled.jsonl → p(1-p), p=test acc | sig_recalib.py (STATS.md 2b) | ✅ |

## Label-definition sensitivity (test split)
| paper number | value found | source → field | status |
|---|---|---|---|
| rule C 0.935 / 30 errors | 0.9346 / 30 | labelled.jsonl → correct_C, test | ✅ |
| rule E 0.989 / 5 errors | 0.9891 / 5 | labelled.jsonl → correct_E, test | ✅ |
| rule C / E AUROCs | see results/analysis/LABEL_SENSITIVITY.md + STATS.md (dAUROC rule C) | ✅ |

## Tier-2 QA (264 questions) — 📊 AGGREGATE-ONLY
`qa_systems.json` stores per-system aggregates only; **per-question correctness/answers
were not persisted** (so McNemar / per-item re-judging are impossible — see
results/analysis_v2/STATS.md §2d and ABSTAIN_SC.md).
| paper number | value found (acc) | source | status |
|---|---|---|---|
| 0.182 / 0.826 / 0.879 / 0.992 (no_memory/vector_rag/pcsg/full_context) | 0.1818 / 0.8258 / 0.8788 / 0.9924 | qa_systems.json → systems[*].accuracy | ✅ 📊 |
| coverage | 0.0 / 0.697 / 0.727 / 0.814 | systems[*].coverage | ✅ 📊 |
| selective accuracy | 0.0 / 0.924 / 0.958 / 0.995 | systems[*].selective_accuracy | ✅ 📊 |
| 0 / 48 fabrication | fabrication_rate = 0.0 (all systems) → 0 fabrications | systems[*].fabrication_rate | ✅ 📊 |
| 113 / 170 / 377 / 460 tokens/question | 113.0 / 169.7 / 377.2 / 460.4 | systems[*].tokens_per_q | ✅ 📊 |

## Write-policy cost sweep (F3)
| paper number | value found | source | status |
|---|---|---|---|
| 5,634,624 / 5,055,109 / 4,750,902 / 4,538,435 / 3,843,204 tokens | identical | cost_curve.json → cells[write_tau=off].cost_tokens | ✅ |
| recalls 0.8667 / 0.8567 / 0.8156 / 0.7656 / 0.6667 | identical | cost_curve.json → cells[write_tau=off].accuracy | ✅ |

## Run totals
| paper number | value found | source | status |
|---|---|---|---|
| 12,787 calls | 12787 | results/QUEUE_STATUS.md → tier1_replay_calibration | ⚠️ reproduces from QUEUE_STATUS, **not** from cost.json |
| 22.5M tokens | **not recoverable** | — | ❌ `cost.json` holds only 1,920 calls / ~600K tokens (a post-reset meter written by a later `--resume` before the cost.json clobber fix; see below). The 22.5M figure was the live tier-1 token total and survives only as prose, not in a committed artifact. |

## Judge validation (LongMemEval, lme_gptoss_20261026b)
| paper number | value found | source → field | script | status |
|---|---|---|---|---|
| judge–human agreement 34/34, κ=1.00 (parsed verdicts) | 34/34, κ=1.000, Wilson 0.898–1.000 | results/lme/lme_gptoss_20261026b/JUDGE_CHECK.md → "all parsed" | scripts/judge_agreement.py (over judge_check_lme_human.csv) | ✅ |
| incl. blank verdicts as wrong: 36/40, κ=0.79 | 36/40, κ=0.789 | JUDGE_CHECK.md → "All 40 rows, blank verdict counted as 0" | scripts/judge_agreement.py | ✅ |

The "Judge validation" paragraph (LLM judge faithfully reproduces the official LongMemEval
grading) maps to `results/lme/lme_gptoss_20261026b/JUDGE_CHECK.md`.

### cost.json clobber — fixed
`_replay` calls `llm.reset_cost()`, and the end of a run wrote `cost.json` from the
(reset) meter, so a later `--resume`/offline stage with 0 new calls overwrote the real
total with near-zero. Fixed in `backend/pcsg/run.py::write_cost_json` (preserves a prior
calls>0 file when the current invocation billed 0 calls); regression test
`backend/tests/test_cost_json_noclobber.py`. This prevents further loss but cannot
recover the already-overwritten 22.5M token total for this run.
