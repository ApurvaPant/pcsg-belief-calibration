# PCSG v2 — Results Summary

> **STATUS: data collection BLOCKED.** The OpenRouter key in `backend/.env` (tail
> `e24f7e`, unchanged) returns 402 "this account never purchased credits" on every
> paid call. STEP 0 (confidence signals) and the STEP 9 reproducibility harness are
> complete; all model-dependent numbers populate the instant a funded key is in `.env`
> and the producing steps run. No number below is fabricated.

## Reproducibility
`python scripts/make_figures.py` regenerates T1–T4 / F1–F3 from `results/` with **no
model calls** (verified). Missing inputs render as PENDING. matplotlib is optional
(figures emit CSV data; PNGs render when it is installed).

## Spend
- budget_usd = 4.00 (config). Paid spend this session: **$0.00** (key non-functional).
- Cost reductions log: none (no paid step ran).

## Step status
| Step | What | Status |
|---|---|---|
| -1 | Simulated-users real run (seed=1000) | BLOCKED (paid). Tooling built; generator self-error rate 4.4% (static). |
| 0  | Confidence signals (verbalized / self-consistency / evidence) | **DONE**, tagged `pcsg-v2-exp`. |
| 1  | Evaluation `judge` role (gpt-4o, stronger than pipeline) | **Infra DONE**; re-scoring/agreement BLOCKED (paid). |
| 2  | Extraction table T1 (dev/test) | BLOCKED (paid). |
| 3  | 100 personas seed=2026, gate-open replay | BLOCKED (paid). datagen ready; seed=1000 reserved for testing. |
| 4  | Calibration F1/T2 | BLOCKED (needs STEP 3 logs + numpy/sklearn). |
| 5  | QA sets (persona + LongMemEval) | BLOCKED (paid + dataset download). |
| 6  | Abstention F2 | BLOCKED. |
| 7  | Write-policy cost curve F3 | BLOCKED (offline replay needs STEP 3 logs). |
| 8  | Baselines T3 | BLOCKED (paid). |
| 9  | Package (make_figures + summary) | **Harness DONE**; tables/figures PENDING until data. |

## Available result (not model-dependent)
### T4 — Paraphrase consistency (MT-1), from `pcsg-v2-rc2` metamorphic run
Mean strict **0.72**, value-level **0.82** (10 families, seed=1000). Full table:
`results/tables/T4.md`. One-line finding: paraphrase invariance is strong on
value+dimension; residual loss is open-key synonymy (gym→going_to_gym/working_out).

## To unblock
Put an OpenRouter key from the account that holds the purchased credits into
`backend/.env` (`OPENROUTER_API_KEY=...`), confirm `GET /auth/key` shows
`is_free_tier:false` / a positive `limit_remaining`, then run steps -1→8 in order;
`make_figures.py` regenerates all artifacts.
