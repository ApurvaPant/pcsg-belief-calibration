# PCSG — Robustness Test Report (pcsg-v2-rc1)

Date: 2026-10-09 · Author: automated robustness run (technical_testing_spec.md)

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
