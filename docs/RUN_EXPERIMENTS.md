# Running the experiments

Everything is driven by `pcsg.run` over `experiments/all.yaml`. Dry-run uses
`oracle_mock` (offline, synthetic, watermarked). Live uses the configured provider.

## 0. Fill in before a live run
- `backend/.env`: a working key (`GEMINI_API_KEY=…` with billing enabled, or
  `OPENROUTER_API_KEY=…` + set `LLM_PROVIDER` accordingly).
- `backend/config.yaml` → `experiment.prices_per_mtok`: real $/Mtok for your models (TODO).
- `backend/config.yaml` → `llm.models.judge`: the strong evaluation judge (TODO; default gemini-2.5-pro).
- `experiments/all.yaml` → `budget_usd`, `n_personas`, `concurrency`, `rate_limit_per_min`.

## 1. Preflight (runs nothing else)
    cd backend
    PYTHONPATH=. ./.venv/Scripts/python -m pcsg.preflight experiments/all.yaml --live
Checks key, one 1-token call per role, billing/credit errors, datasets at pinned
revision, disk, and the projected live cost vs budget_usd.

## 2. Dry-run (free, offline — verify the whole chain)
    PYTHONPATH=. ./.venv/Scripts/python -m pcsg.run experiments/all.yaml --dry-run
    cd .. && python scripts/make_figures.py
Produces every artefact under results/ watermarked "DRY RUN — SYNTHETIC".

## 3. Live run
    cd backend
    PYTHONPATH=. ./.venv/Scripts/python -m pcsg.run experiments/all.yaml --live
    cd .. && python scripts/make_figures.py
Uses the committed max_tokens (1024), never the test-only 512 cap.

## Resuming after an interruption
A checkpoint (`results/runs/<run_id>/checkpoint.json`) is written after every
persona. Re-run with `--resume <run_id>` to skip completed personas/QA batches —
completed work is never re-paid. A credit/rate-limit error stops cleanly with a
resumable checkpoint; fix billing and resume.

## Human-review files (appear during the run; do not block)
- `backend/datagen/verify_sample.csv` — 50 generated messages for review.
- `results/eval/judge_check.csv` — 100 judge decisions for review.

## Expected runtime (live, 100 personas)
~2,500 user turns × ~7 model calls ≈ 17.5k calls. Wall-clock depends on the
provider's rate limit (`rate_limit_per_min`); preflight prints the projected cost.
