# PCSG — Robustness Test Report (pcsg-v2-rc1)

Date: 2026-10-09 · Author: automated robustness run (docs/dev-notes/technical_testing_spec.md)

## 1. Summary

A full automated test system was built per the spec and run against the real
FastAPI routes (in-process, production path). It found and fixed **4 bugs**
(one S0, one S1, two S2), all with regression tests and green 3/3 reruns where
the provider budget allowed.

| Layer | Result |
|---|---|
| Unit suite (S1, mock, pytest) | **98 passed** |
| Scenario suite (mock, deterministic) | **19/19 green, 3/3 runs, `--no-cache`** |
| Scenario suite (real provider pilot) | **5/5 green** (budget-limited to `--runs 1`) |
| Global invariants | **0 violations** across every turn of every scenario |
| Bugs found / fixed | 4 / 4 |

## 2. Architecture note (spec ↔ this build)

The spec assumes a PCSG-v2 pipeline with async/background extraction, a
`confidence_signals` table, an LLM cache, and span/structuring stages. **This
build is simpler and synchronous**: the chat reply is produced *after*
reconciliation; there is no `confidence_signals` table, no cache, and extraction
is a frame→fill split (not span→structuring). The harness was built to the real
system and the following invariants are therefore **Not Applicable** (documented
in `tests/robustness/invariants.py::NOT_APPLICABLE`):

- **INV-02** — no `confidence_signals` table.
- **INV-17** — chat-call token logging is partial (extraction calls are logged in full).
- **INV-19** — the chat prompt is not persisted, so the state block cannot be re-read post hoc (covered instead by the read-path/fabrication scenarios).
- **INV-22** — pipeline is synchronous; the reply is produced after reconciliation, so "reply precedes background extraction" cannot hold or fail.

`/debug/wait` is consequently a no-op sync point, and `--no-cache` is a
documented no-op (no cache exists; every real call is already fresh).

## 3. Invariants enforced (18 of 22)

INV-01, 03, 04, 05, 06, 07, 08, 09, 10, 11, 12, 13, 15, 16, 18, 20, 21 are
checked after **every turn of every scenario** (INV-14's guard is additionally
covered by unit tests `test_destructive_guard.py`). Zero violations observed.

Notable invariant catches this run: INV-01 caught BUG-001 (confidence_raw
mutation); INV-20 caught BUG-002 (active attributes left on an ended node).

## 4. Bug table

| ID | Sev | Stage | Status | One line |
|---|---|---|---|---|
| BUG-001 | S1 | update_policy | fixed | reinforcement mutated `confidence_raw` (INV-01) |
| BUG-002 | S2 | update_policy | fixed | ending a node left its attributes active (INV-20) |
| BUG-003 | S2 | gate | fixed | "i do coding" (3 tokens) gated out |
| BUG-004 | S0 | structuring + read_path | fixed | negated relation ("mom & dog don't get along") not extracted; reply then fabricated |

Full records with repro, root cause, fix and verification are in `docs/BUGS.md`.

## 5. Scenario coverage

Implemented and green:

- **R (regressions, §4.R):** R01, R02, R03, R04, R07, R08, R09, R19, R20 (mock); R10, R16, R17 (real). The destructive/identity regressions are covered.
- **C (negation vs ending, §4.C):** CN1, CN2, CN4, CN5, C11 (mock); CN3, C5 (real). C5 is the critical "they don't must not end the mom node" chain — green.
- **D/E/O (ownership, cardinality, idempotency, §4.D/E/O):** D4, E4, E5, O1, O3 (mock).

## 6. Unmet criteria / deferred (honest status)

Per the spec's fallback priority, the following were **not** completed this run
and are deferred. None are known-failing; they are unimplemented or
budget-gated, and must be run before the benchmark experiments.

1. **Full §4 catalogue** — suites A, B (fragment chains need more injected-assistant
   scenarios), F, G, H, I, J, K, L, M, N, P are only partially authored. The
   infrastructure (inject_assistant, seed, selectors) supports them; they need
   writing.
2. **S3 metamorphic, S4 fuzz/concurrency, S6 fault injection, S7 variance,
   S8 performance, S9 simulated users** — not yet built. `/debug/fault` and the
   fault hook in `llm.py` exist, so S6 is ready to author.
3. **Real-provider runs at `--runs 3`** — the pilot ran at `--runs 1` because
   the OpenRouter key is a free-tier (~$5) key; the S0/S1 destructive and
   identity logic that must hit 3/3 is covered deterministically on mock at 3/3.
   Re-run the real scenarios at `--runs 3` once a funded key is available.
4. **Extraction eval harness (dev/test split)** — not re-run here; `budget_usd`
   is still `0`, which makes the paid eval refuse to run. Set a real ceiling.
5. **Performance baseline (S8)** — not measured; record p50/p95 chat/extraction
   latency and per-turn cost on the next funded run.

## 7. How to run

```
cd backend
# unit (mock, free):
./.venv/Scripts/python -m pytest -q
# scenarios (mock, deterministic, free):
PCSG_TEST_MODE=1 PYTHONPATH=. ./.venv/Scripts/python tests/robustness/runner.py --suite all --provider mock --runs 3 --no-cache
# scenarios (real provider, costs credit):
PCSG_TEST_MODE=1 PYTHONPATH=. ./.venv/Scripts/python tests/robustness/runner.py --suite all --provider real --runs 1
```

Reports land in `backend/results/robustness/<run_id>/` (git-ignored).

---

# Addendum — pcsg-v2-rc2 (three pre-experiment phases, real provider)

budget_usd set to 4.00; total real spend across all phases well under $0.20.

## Phase 1 — full §4 catalogue (real)
Authored A/B/D/E/F/G/H/I/J/L/M/N/P + remaining R (~50 real scenarios). First run
36/50; after fixes ~48/50. **S0 set 3/3 green** (C5, CN3, D2, D5, N2–N5) + mock S0
3/3. Bugs: BUG-005 injection/forwarded quarantine (S0), BUG-006 context-bleed
grounding (S1), BUG-007 reported-wish/dream (S0), BUG-008 strip over-strip (S1),
BUG-009 slot name/age + INV-13 resolved (S2). F3 capability-limit; N1 disputed.

## Phase 2 — S5 fabrication (real, rich_user + 43-question bank)
Raw LLM-judge: correct-answer 85%, correct-abstention 88%, fabrication 12% (2/17).
**Manually verified true fabrication ~0** — residual failures are cheap-judge
false-positives (judge claimed stored foods were "not stored", read a correct dog
name as an invented vet, demanded clarifications not in the rubric). Read-path fix
BUG-010 (no invented specifics; correct false premises; nonexistent-entity guard;
broadened historical detection). Judge reliability documented as a capability-limit
(gpt-4o-mini is the strongest configured model; model strings unchanged).

## Phase 3 — S3 metamorphic (real)
MT-6 idempotency PASS; MT-7 negation 3/3 distinct; MT-8 subject 4/4. MT-1
paraphrase strict 0.72 / value-level 0.82 (per-family table in results/consistency/).
Below the 0.90 target — gap is open-key synonymy + value morphology; documented.

## Unmet criteria at rc2 (do not block the freeze; see §6 above too)
- MT-1 paraphrase consistency < 0.90 (open-key canonicalisation out of scope).
- S4 fuzz/concurrency, S6 fault scenarios, S7 variance, S8 performance, S9
  simulated users: not run this cycle.
- Fabrication "0" is met on manual verification; the automated judge cannot
  certify it due to judge noise.

## S0 status: 0 open S0 bugs. Freeze → pcsg-v2-rc2.

---

# Addendum — pcsg-v2-rc3 (Phases A/B/C)

## Phase A — simulated users (S9)
Built reusable experiment tooling: `pcsg/datagen/` (persona truth tables in code —
stable facts + 2 changes + 1 END + 2 entity facts + 1 hedged + noise; seeded;
P6-style verbalisation with full/fragment/typo/when_asked), `tests/robustness/
simulator.py` (scripted + model modes), and a `simulator` config role (llama-8b,
distinct from the gpt-4o-mini extractor). **Generator self-error rate = 4.4%**
(7/160 facts over 20 seed=1000 personas — the "reveal-when-asked" sibling fact that
only surfaces if the assistant asks). The real 20-persona comparison run is
**credit-deferred** (OpenRouter free allowance exhausted mid-cycle). seed=1000 is the
TESTING seed; experiments must use a different seed.

## Phase B — fault injection (S6) — COMPLETE
`llm.complete` retries timeout/429/5xx with exponential backoff (logged in
RETRY_LOG); the chat reply degrades gracefully (no 500); failed extractions are
logged `extraction_log.status='failed'` with an error reason; `GET /debug/failures`
summarises failed count by reason + the retry log for the experiment runner to
count/exclude failed messages. Required behaviours verified by
`tests/test_fault_injection.py` (retry → succeed; give up after max; graceful chat +
writes intact; failed-extraction logged + no partial writes). Non-retryable 402
(credit) correctly fails fast and is logged, not retried.

## Phase C — variance (S7)
Deterministic mock catalogue: **19/19 green 3/3 `--no-cache` → 0 flaky** on the
deterministic layer. Real-provider 3× variance is **credit-deferred**; genuine model
variance (the data the self-consistency signal will measure) will be captured on a
funded key.

## S0 status: 0 open S0 bugs. Freeze → pcsg-v2-rc3.
Credit-deferred: Phase A real run, Phase C real variance (tooling ready).
