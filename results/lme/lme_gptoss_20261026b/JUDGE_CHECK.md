# LLM-judge human validation — LongMemEval (lme_gptoss_20261026b)

Judge `minimaxai/minimax-m3.1-flash` vs. a human rater on a 40-row stratified sample (`judge_check_lme_human.csv`, UTF-8-BOM, read utf-8-sig). Offline; no API calls.

## Agreement on rows with a parsed judge verdict (judge_verdict ∈ {0,1})

| subset | n | agree | raw agreement | Cohen's κ | Wilson 95% CI |
|---|---|---|---|---|---|
| all parsed | 34 | 34 | 1.000 | 1.000 | 0.898–1.000 |
| knowledge-update | 23 | 23 | 1.000 | 1.000 | 0.857–1.000 |
| single-session-preference | 11 | 11 | 1.000 | 1.000 | 0.741–1.000 |

- Answers beginning with "I don't know": **21** of 40.

## Blank judge verdicts

6 rows had a blank/unparsed judge verdict (judge emitted no yes/no):

| question_id | system | question_type | human_correct |
|---|---|---|---|
| 8a2466db | full_context | single-session-preference | 1 |
| 38146c39 | full_context | single-session-preference | 1 |
| 8a2466db | vector_rag | single-session-preference | 1 |
| 195a1a1b | vector_rag | single-session-preference | 1 |
| 195a1a1b | pcsg | single-session-preference | 0 |
| 07741c44 | pcsg | knowledge-update | 0 |

- Of the 6 blank-verdict rows, the human marked **4** correct.

## All 40 rows, blank verdict counted as 0

- agreement **36/40** (raw 0.900, Wilson 95% CI 0.769–0.960)
- Cohen's κ **0.789**

## Interpretation

On the 34 rows the judge actually scored, it agrees with the human 34/34 (κ=1.00): the official LongMemEval prompts are reproduced faithfully. The only disagreements in the full-40 view come from blank verdicts — truncated/JSON-wrapped answers the judge could not parse (counted wrong in T5) — some of which a human would credit; see `T5_final.md` §SSP.
