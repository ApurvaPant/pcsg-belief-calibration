# PROGRESS — extractor improvement + PCSG v2 revamp

Running log. Newest entry on top. Numbers are recorded as they are produced;
nothing here is aspirational.

---

## 2026-10-08 — Fix: question–answer resolution for fragment replies

### What was broken
Short answers to the assistant's own questions were gated out or extracted in
isolation, losing their meaning (live evidence: "8 months", "reading books",
"technical", "deep learning" all dropped or mis-attached).

### What was built (fix steps 1–5)
1. **Q–A resolution (P8), before the gate.** When the previous assistant turn is
   a question and the reply is a fragment (no first-person subject), the cheap
   model rewrites it into one standalone first-person sentence; the gate, span
   extraction and structuring then run on the RESOLVED text, while the evidence
   quote keeps the user's original words (`messages.resolved_content` stores the
   rewrite). Prompt P8 added; mock handler `llm.mock_qa_resolve` added so the
   path is exercised offline. `app/pipeline.py`, `app/llm.py`, `app/db.py`.
2. **Every assistant question opens a pending slot.** Two types — `attribute`
   (known node + attribute) and `open` (topic, node optional). Any turn while a
   slot is open gets the salience floor of 60 (slots now expire after 2 turns).
   `_detect_slot` generalized; `pending_slots` gained `slot_type` + `topic`.
3. **Refinement chains.** An open slot bound to a node created/refined recently
   attaches the answer as an attribute of THAT node ("technical" → genre,
   "deep learning" → topic on the reading habit) instead of making a new node —
   unless the user expresses a new interest (love/like/follow), which creates a
   node as before. `habit` gained `genre`/`topic`/`detail` attributes.
4. **Cross-dimension duplicates.** Before creating a MULTI node, a same-value
   node under another dimension is reinforced (logged `dimension_conflict`)
   instead of duplicated — unless the two may legitimately coexist (a language
   that is both a `skill` and a `preference`). The structuring prompt now states
   that how the user wants responses written is always `communication_style`.
   `app/update.py`, `app/ontology.py`, `app/extraction.py`.
5. **Eval chains.** Harness `history` now accepts `{role, text}` turns incl.
   assistant turns; added an `attributes_on` graph check. Seeded
   `backend/tests/extractor_eval_cases.json` with the three DEV chains (dog
   duration, books chain, open answer) and three TEST chains with different
   wording (cat duration, painting chain, open answer).

Also fixed a non-determinism: `_get_open_slot` now breaks `created_at` ties by
insertion order (`rowid`), so the newest slot always wins.

### Before/after (mock provider; ablation via `PCSG_DISABLE_QA_FIX=1`)
| split | before (fix off) | after (fix on) |
|-------|------------------|----------------|
| dev   | 1/3 = 33.3% (F1 50.0) | **3/3 = 100%** (F1 100) |
| test  | 1/3 = 33.3% (F1 50.0) | **3/3 = 100%** (F1 100) |

All 76 unit tests pass (69 existing + 7 new in `test_qa_resolution.py`). A
`PCSG_DISABLE_QA_FIX` ablation toggle was added so the before/after is measured
on identical cases.

### Honest caveats
- These scores are on the **mock** provider over the **six** chains directed in
  the spec, which I authored — not the 45-case human-labelled baseline (still
  absent) on a real provider. The mock's P8 + extraction are deterministic
  stand-ins; the pipeline logic they exercise is provider-agnostic. A real
  before/after still needs the 45-case set and a `budget_usd`.
- `extractor_eval_cases.json` currently holds only these six chains; merge the
  45 baseline cases into its `cases` array when provided.

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
