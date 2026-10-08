# PROGRESS — extractor improvement + PCSG v2 revamp

Running log. Newest entry on top. Numbers are recorded as they are produced;
nothing here is aspirational.

---

## 2026-10-08 — Phase 0 / STEP 1 (harness) built; BLOCKED on two inputs

### TL;DR
The evaluation harness is built and validated. I **cannot record the baseline**
yet, because the two inputs the whole program depends on are not present:

1. **The eval set `backend/tests/extractor_eval_cases.json` is missing.**
   Searched the entire repo by filename and by content markers
   (`graph_after`, `value_any`, `assistant_last`); it exists nowhere — those
   markers appear only inside the spec text. The spec says to copy it into
   `backend/tests/` first; that step did not happen.
2. **`budget_usd` was undefined.** Both specs gate every paid run on it. I added
   `experiment.budget_usd` to `config.yaml`, defaulted to `0.0`, which the
   harness treats as "unset" and refuses all paid runs.

I did **not** fabricate the eval set. A baseline computed against cases I wrote
— then graded against a graph I also control — would be a fake number and would
destroy the exact guarantee the methodology exists to protect ("every change
justified by a score"; "never put an eval message in a prompt"; test split
unseen until STEP 6).

### Methodology decision (why I stopped before changing extractor behaviour)
The spec's first rule is **measure before changing**, and the baseline must be
recorded on the *current, unchanged* system. Since I cannot record it yet,
implementing STEP 2+ now would silently alter the system the baseline is
supposed to describe. So I built everything that does **not** change extractor
behaviour, tagged the current code as the baseline snapshot, and am holding all
behaviour changes (STEP 2 tier-0 rules, STEP 3 call restructure, STEP 4 prompts,
STEP 5 resolution, and all of PCSG v2) until a baseline exists.

### Built this session
- **`backend/scripts/eval_extractor.py`** — the full STEP 1 harness:
  - Fresh DB per case; runs the real pipeline over `history`, injects
    `assistant_last` (opening a pending slot exactly as the live pipeline does),
    then scores the TARGET `message`.
  - Scores: overall + per-category pass rate; assertion-level precision/recall/F1
    (subject+dimension+key+value+operation all required); per-field accuracy
    (subject/dimension/key/value/operation) to show *which* decision fails;
    false-write rate on negatives; `graph_after` checks (`still_active`,
    `superseded`, `ended_not_deleted`, `unchanged`, `must_not_exist`,
    `active_count`, `no_value_longer_than_words`).
  - Per-stage tokens + cost (tier1 / frame / fill / chat), priced from config.
  - Flags: `--split dev|test|all`, `--case <id>`, `--runs N`, `--dry-run`.
  - **Held-out guard:** `--split test` is refused unless `--i-am-at-step-6`.
  - **Budget guard:** projects cost from a 3-case pilot and refuses to run if the
    projection exceeds `experiment.budget_usd`; refuses immediately (zero spend)
    if the budget is unset.
  - Temperature 0 forced on every call during evaluation.
  - Saves every run to `backend/eval_results/<timestamp>_<prompt_version>.json`.
- **`config.yaml` / `app/config.py`** — new `experiment` block:
  `budget_usd` (hard spend ceiling) and `prices_per_mtok` (projection only),
  plus `ExperimentSettings.cost_usd()`.
- Validated the harness end-to-end on the **mock** provider with a throwaway
  case file (kept out of the repo): 4/4 pass, field-accuracy + cost breakdown
  render, test-split refusal and budget-abort both fire. Existing suite: **69/69
  pass**.

### Files changed
- `backend/scripts/eval_extractor.py` (new), `backend/scripts/__init__.py` (new)
- `backend/config.yaml`, `backend/app/config.py`
- `.gitignore` (ignore `eval_results/`, `results/`, `.llm_cache/`, `datasets/`,
  human-review CSVs)
- `docs/PROGRESS.md` (this file)

### Numbers
- Baseline: **not yet recorded** (blocked on the eval set).

### To unblock (one action each)
1. Put the 45-case file at `backend/tests/extractor_eval_cases.json`. Its schema
   is documented at the top of `scripts/eval_extractor.py`; the harness also
   accepts an `EVAL_CASES_PATH` override for a temporary location.
2. Set `experiment.budget_usd` in `backend/config.yaml` (e.g. `10.0`), or export
   `BUDGET_USD`.

Then: `python -m scripts.eval_extractor --split dev --runs 3` records the
baseline, and STEP 2+ proceed.

### Rough cost picture (for setting the budget)
- **Phase 0 eval** (dev or test, ×3 runs): small — on the order of cents to ~USD 1
  per 3-run pass on `gpt-4o-mini` + `llama-3.1-8b` (≈3 model calls per message).
- **PCSG v2** is where real money/time go: self-consistency k=5 over ~1,000
  synthetic assertions; the write-policy grid (5×4 cells); LongMemEval/PersonaMem
  QA over multi-session histories; four baselines incl. Mem0. Each of these has
  its own pilot + budget guard before running.

### Needs human review (generated later; not yet produced)
- `backend/pcsg/datagen/verify_sample.csv` — 50 generated persona messages, to
  confirm each expresses its scripted event (PHASE 3a.4).
- `backend/eval/judge_check.csv` — 100 judge decisions to hand-label for κ
  (PHASE 3b.4).

### Open issues / risks to confirm when unblocked
- **Python downgrade:** the env was rebuilt on **Python 3.9** (the original 3.13
  was uninstalled). PCSG v2 pulls numpy/scipy/scikit-learn/matplotlib (have cp39
  wheels), `netcal`, `datasets`/`huggingface_hub`, and `mem0ai` — some have large
  dependency trees and a few may lack cp39 wheels on a machine with no C++ build
  tools. Each will be validated at the phase that needs it (PHASE 1/3/4/7) and
  any install failure recorded here.
- **External datasets** (LongMemEval, PersonaMem) require download (network, disk,
  possibly an HF token / licence acceptance). Versions will be pinned in the run
  config when ingested.
- **`extraction_log.rules_fired`** column does not exist yet; STEP 2 adds it.
