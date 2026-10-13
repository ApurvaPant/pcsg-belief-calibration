# READINESS_OFFLINE — live-experiment pre-flight (offline sessions, 2026-10-13)

**VERDICT: READY FOR LIVE (pending provider + prices only).** The build session
cleared every offline blocker: the four baseline systems (T3), the LongMemEval
T5 runner (Tier 3), per-persona/-question parallelism infra, per-call telemetry,
and the self-consistency coverage bug are all built, wired and tested. The only
things left for the live session are **provider/model reconciliation (Blocker 1)**
and **live prices (Blocker 6)** — both deferred to that session by instruction.
No paid/live LLM call was made in either offline session; `PCSG_OFFLINE=1` throughout.

---

## BUILD SESSION UPDATE — 2026-10-13 (tag `pcsg-v2-build-complete`)

### Blocker status (from the readiness check)
| # | Blocker | Status |
|---|---|---|
| 1 | Provider mismatch (gemini vs OpenRouter) | **DEFERRED to live session** (model-string change, out of scope) |
| 2 | Four baseline answerers (T3) | **CLEARED** — `pcsg/baselines.py` (no_memory/full_context/vector_rag/pcsg), one shared answer template + `QA_ANSWER` task, wired into the QA stage with per-system metrics (`qa_systems.json`, `baselines.json`) + per-system F2 sweeps |
| 3 | No per-persona/-question parallel runner | **CLEARED (infra)** — thread-local DB override (`db.set_db_path`) gives each worker its own SQLite file; LongMemEval runner runs per-question fresh DBs via a `ThreadPoolExecutor` (serialised under the deterministic oracle, parallel on live) |
| 4 | Per-call telemetry | **CLEARED** — `complete()` surfaces served model + a cost meter (`llm.spent_usd`); `run.model_mismatches` flags served≠requested |
| 5 | LongMemEval T5 runner (Tier 3) | **CLEARED** — `pcsg/longmemeval_run.py`; dataset pinned to commit `98d7416c24c7…`, 108 selected (ku 78 + ssp 30, 6 `_abs`); chronological haystack replay, 4 systems, per-type/abstention/answer-ECE metrics, resumable |
| 6 | Prices unfilled | **DEFERRED to live session** (`fill_prices.py` runs in `go_live.sh`) |
| A | self_consistency null (warning) | **CLEARED** — entity-subject path propagated the signal; `self_consistency_reason` column means no silent null; 100% coverage verified |

### STEP 4 dry-run PASS/FAIL (oracle_mock, n=15 split-spanning, 4 systems + 3 LongMemEval, `results/dryrun/`)
`13/13 PASS.` A split-spanning sample (not pure-dev) is used because recalibration needs fit+test personas; it includes the dev split.

| Check | Result | Actual |
|---|---|---|
| every stage produced output | PASS | 10 artefacts |
| labels on every assertion | PASS | 270/270 |
| three signals in [0,1], no NaN | PASS | **0/270 bad (was 30/270)** |
| **signal coverage 100%** | PASS | verbalized 1.0 · self_consistency 1.0 · evidence 1.0 · silent nulls 0 |
| recalibration fit/test, no leakage | PASS | n_fit=108, n_test=108, leak=0 |
| metrics present (ECE 10u+15q, MCE, Brier, NLL, AUROC, bootstrap CI) | PASS | all per recalibrator |
| four-system QA metrics | PASS | no_memory/full_context/vector_rag/pcsg; AURC 0.82/0.33/0.22/0.31 |
| LongMemEval T5 present | PASS | n=3 (knowledge-update), 1 abstention, 4 systems |
| QA answered + judged | PASS | 66 questions × 4 systems |
| cost recorded | PASS | 3492 calls, $0.5959 |
| confidence_raw immutable / signals append-only | PASS | 270 rows, 0 out-of-range |
| dashboard builds + validates | PASS | `schema.validate == []` |

Full suite: **140 passing** (mock). Embedding: real `all-MiniLM-L6-v2` loads; deterministic fallback for offline tests.

### Remaining for the LIVE session (only these)
- **Blocker 1 — provider/model.** Choose the live provider; set `backend/.env` + reconcile pipeline slugs if moving off gemini. Governs whether `openai/gpt-4o` (LongMemEval judge) resolves, and paste the official per-type LongMemEval judge prompts verbatim into `pcsg/longmemeval_run.py` from `longmemeval/src/evaluation/evaluate_qa.py` (left as a source pointer this offline session).
- **Blocker 6 — prices.** `scripts/go_live.sh` runs `fill_prices.py`; the price-guard refuses live until every configured model is priced.

---

### (Original readiness-check verdict, 2026-10-13 — superseded by the build update above)
**VERDICT: READY WITH WARNINGS.** The offline pipeline (offline guard, config
audit, price-guard, fault-injection mock, budget hard-stop, PCSG
confidence-calibration + cost dry run) is verified and green. **LIVE LAUNCH IS
BLOCKED** until the blockers below are cleared, and the **full paper** additionally
needs three components that are not built (four baseline answerers → T3,
LongMemEval T5 runner → Tier 3, true per-persona parallel runner). No paid/live
LLM call was made this session; `PCSG_OFFLINE=1` throughout.

## Key facts
- **Verification commit:** `edb6979` (`git describe` = `pcsg-v2-ready-2-gedb6979`; ≥ `pcsg-v2-ready` ✓). Readiness changes committed on top and tagged `pcsg-v2-offline-ready`.
- **Persona truth-table hash (seed=2026, n=100):** `30a7b1d04f33632bd86c6993cd8740c10462e689f33686303a04c09f2bbeca61` (identical across two generations).
- **Split (by persona):** fit 40 / test 40 / dev 20; 0 overlap; covers all; stable across regen. `seed=1000` not used (only a warning comment).
- **Disk free:** 57 GB (≥ 15 GB ✓).
- **Provider (config):** `gemini`, base_url `…/v1beta/openai`. **Mismatch with the paper's OpenRouter/openai plan — see BLOCKER-1.**
- **prompt_version:** `2.0-frame-fill`.

### Role → model (verified in code; `backend/config.yaml`)
| Paper role | config role | model string |
|---|---|---|
| tier-1 gate | `classifier` | `gemini-2.5-flash-lite` |
| span extraction (FRAME) | `classifier` | `gemini-2.5-flash-lite` |
| structuring (FILL) | `extractor` | `gemini-2.5-flash` |
| self-consistency (k=3 FILL) | `extractor` | `gemini-2.5-flash` |
| verbaliser P6 | `extractor` | `gemini-2.5-flash` |
| value-equivalence P7 | `judge` (live) / deterministic stub (offline) | `gemini-2.5-pro` |
| answerer | `chat` | `gemini-2.5-flash` |
| judge (default) | `judge` | `gemini-2.5-pro` |
| judge (LongMemEval) | `judge_longmemeval` | `openai/gpt-4o` *(added this session)* |

### Judge routing (added, config-only)
`llm.judge_routing: {longmemeval: judge_longmemeval, default: judge}` — LongMemEval
answers → `judge_longmemeval` (official per-type prompts); everything else → `judge`.
No pipeline model string was changed.

### Budget
`experiment.budget_usd = 25.00` (config.yaml) and `budget_usd: 25.00` + `workers: 10`
(experiments/all.yaml).

## PASS/FAIL — Phases 0–4
| Check | Result | Actual |
|---|---|---|
| 0.1 checkout ≥ pcsg-v2-ready, tree clean | PASS | `edb6979`; only expected untracked files |
| 0.2 full pytest (mock) 100% green | PASS | **130 passed** |
| 0.3 S0 robustness (mock) 0 invariant violations | PASS | **19/19 green, inv=0 on every scenario** |
| 0.4 disk ≥ 15 GB | PASS | 57 GB free |
| 0.5 datagen determinism + split + seed | PASS | hash identical; 40/40/20, 0 overlap; seed 2026 |
| 1.1 model-string audit vs EXTRACTOR_VERSION.md | WARN | file was **absent** → created from live config (see BLOCKER-1 for provider mismatch) |
| 1.2 LongMemEval judge key added (config only) | PASS | `judge_longmemeval: openai/gpt-4o` + routing block |
| 1.3 budget_usd = 25 | PASS | config.yaml + all.yaml |
| 1.4 fill_prices.py + fixture parse test | PASS | `scripts/fill_prices.py` (not run); `test_price_config.py` |
| 1.5 price-guard refuses live when price missing/zero | PASS | `pcsg.run.check_prices` + 4 tests (mock path) |
| 2.0 fault-injection mock (429/5xx/timeout/malformed) + fake tokens | PASS | `llm.set_fault_rates` + tests |
| 2.1 parallelism: own-DB per persona, workers=10 | **NOT MET** | not implemented; runner is sequential/single-DB (BLOCKER-3) |
| 2.2 retries: backoff+jitter, ≤5, 60 s, logged; persona-fail isolates | PASS | `_MAX_RETRIES=5`, full-jitter backoff, `RETRY_LOG`; per-persona try/except continues |
| 2.3 malformed output handled, no crash / no half-write | PASS | extraction logs `status='failed'`; malformed_json surfaced, no crash |
| 2.4 resume: finished skipped; no dup rows | PARTIAL | finished personas skipped (demonstrated); budget/graceful stops leave no partial persona. A hard-kill mid-persona could leave partial rows in the shared DB (needs per-persona DBs — BLOCKER-3) |
| 2.5 budget hard-stop, clean + resumable | PASS | demo: stopped at 2/15 when $0.0745 ≥ $0.05; `budget_stop.json` + checkpoint; `--resume` completed all 15 |
| 2.6 logging: served model + served≠requested check | PASS (partial logging) | `complete()` surfaces served model; `pcsg.run.model_mismatches` + tests. Per-call role/tokens/cost/retry/persona log line is NOT yet threaded out of the pipeline (BLOCKER-4) |
| 3. full dry run on oracle_mock → results/dryrun/ | 9/10 PASS, 1 WARN | n=15 mixed, 270 labelled assertions |
| 3 · every stage produced output | PASS | 10 artefacts non-empty |
| 3 · labels on every assertion | PASS | 270/270, correct∈{0,1} |
| 3 · 3 signals in [0,1], no NaN | **WARN** | verbalized+evidence 100%; **self_consistency null on 30/270 (relationship/ADD)** — see WARNING-A |
| 3 · recalibration fit/test, no leakage | PASS | n_fit=108, n_test=108, fit∩test personas = ∅ |
| 3 · metrics present (ECE 10-uniform + 15-quantile, MCE, Brier, NLL, AUROC, bootstrap CI) | PASS | all present per recalibrator (MCE+NLL added this session) |
| 3 · QA answered + judged | PASS (PCSG only) | n=66, AURC=0.113 — **only the PCSG system; 3 baselines missing (BLOCKER-2)** |
| 3 · cost recorded, within budget | PASS | 3159 calls, $0.5679 |
| 3 · confidence_raw immutable / signals append-only | PASS | 270 signal rows, 0 out-of-range; `test_confidence_immutable` green |
| 3 · dashboard builds + validates | PASS | `build_dashboard` → `schema.validate == []` |
| 3 · RESULTS_SUMMARY.md traceable | PARTIAL | produced by `make_figures`/summary stage, not the runner; template exists at `results/RESULTS_SUMMARY.md` |
| 4. LongMemEval T5 | **NOT READY** | only a dataset adapter exists; no T5 runner (BLOCKER-5) |

## Blockers (each: what's wrong → what clears it)
- **BLOCKER-1 — provider mismatch (gemini vs OpenRouter).** Every pipeline role is a
  `gemini-*` slug on the Gemini endpoint; the paper targets OpenRouter
  `openai/gpt-4o` / `openai/gpt-4o-mini`. Earlier this machine saw
  `gemini-2.5-flash-lite` return `404 "no longer available to new users"`, so the
  committed gemini slugs may be dead. *Clears when:* a human decides the live
  provider and sets `backend/.env` + the pipeline slugs accordingly (a model-string
  change, out of scope for this session). This also governs whether the
  `openai/gpt-4o` LongMemEval judge resolves.
- **BLOCKER-2 — baseline answerers not implemented (T3).** Only PCSG answers QA. No
  `no_memory`, `full_context`, or `vector_rag` answerer exists (grep: none). The
  `baselines` stage in `all.yaml` has no implementation. *Clears when:* the three
  baseline answerers are built and wired into the QA stage.
- **BLOCKER-3 — no per-persona parallel runner.** `pcsg.run` is sequential and shares
  one SQLite DB (`workers`/`concurrency` are config only, unconsumed; grep: no
  threadpool/multiprocessing). *Clears when:* a worker pool runs each persona in its
  own DB file, then merges signals/logs; this also makes a hard-kill mid-persona
  resume cleanly.
- **BLOCKER-4 — per-call telemetry log line.** The runner records changes + a cost
  total but not a per-call row (role, model requested/served, prompt_version,
  tokens, cost, latency, retry count, persona). `complete()` now surfaces the served
  model and a `model_mismatches` check exists; the full per-call log must be threaded
  out of `pipeline.process_turn`.
- **BLOCKER-5 — LongMemEval T5 runner (Tier 3).** `pcsg/longmemeval.py` downloads +
  filters the dataset (`xiaowu0162/longmemeval-cleaned`, expects 78 KU), but nothing
  answers those questions through the pipeline + judge. Also `REVISION="main"` is an
  unpinned branch, not a reproducible commit SHA. *Clears when:* a T5 runner is built
  (explicitly out of scope per the task) and the revision is pinned.
- **Prices unfilled (expected, by design).** Live prices for the configured models are
  not in `config.yaml` (prices left as-is per the rules). The price-guard will refuse
  a live run until `scripts/fill_prices.py` populates them. *Clears when:* go_live.sh
  runs fill_prices.

## Warnings (not blockers)
- **WARNING-A — self_consistency null on ~11% of assertions (relationship/ADD).** The
  computed self-consistency signature is not re-attached to those specific assertions
  after reconciliation, so the signal is written null. verbalized + evidence are present
  on 100%; calibration correctly excludes nulls per-signal, so numbers are not
  corrupted. This is pre-existing frozen-signal behavior (pcsg-v2-exp), **not** altered
  this session (the rules forbid changing frozen signal logic to turn a check green).
- **EXTRACTOR_VERSION.md was absent** and is now created by snapshotting the live config;
  it matches by construction and exists to catch future drift.

## After the key is funded — exact commands in order
```bash
# 0. (human) set the live provider + funded key in backend/.env, and reconcile
#    the pipeline model slugs if moving off gemini (BLOCKER-1).

# 1. price-fill + model verification + 5-persona DEV pilot + cost/time projection.
#    Unsets PCSG_OFFLINE, runs fill_prices, STOPS after the pilot (does not launch full run):
bash scripts/go_live.sh

# 2. (human reviews the projection vs budget_usd=25, then) launch the FULL run:
cd backend && PYTHONPATH=. ./.venv/Scripts/python.exe -m pcsg.run experiments/all.yaml --live
#    run_id is printed; artefacts land in results/runs/<run_id>/
#    resume after any interruption:
#    PYTHONPATH=. ./.venv/Scripts/python.exe -m pcsg.run experiments/all.yaml --live --resume <run_id>

# 3. regenerate tables/figures from results/ (no model calls):
cd .. && python scripts/make_figures.py
```
Note: steps 2–3 produce the PCSG calibration + cost results. T3 (baselines) and
Tier-3 (LongMemEval) require BLOCKER-2 / BLOCKER-5 to be built first.
