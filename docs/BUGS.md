# PCSG — Bug ledger (robustness testing)

Severity: S0 corrupts state · S1 wrong state/reply · S2 missed info · S3 structural.
A bug is **closed** only after a green 3/3 rerun with `--no-cache` + a regression test.

---

## BUG-001  [S1]  [update_policy]  status: fixed
Found by: robustness/R04 (INV-01 violation, run 1 of 3)
Repro: `tests/robustness/runner.py --suite regressions --case R04 --provider mock`
Expected: `confidence_raw` is immutable after insert (INV-01 and the project's hard rule "Never modify confidence_raw after insert").
Actual: `_reinforce` ran `UPDATE nodes SET confidence_raw = noisy_or(...)` on every re-observation, so repeating "my girlfriend is hailey" raised confidence_raw 0.9 → 0.99.
Root cause: reinforcement combined evidence into `confidence_raw` instead of `confidence_calibrated`, rewriting the immutable raw signal.
Fix: `app/update.py::_reinforce` now leaves `confidence_raw` untouched and accumulates evidence into `confidence_calibrated` via Noisy-OR (monotonic, capped < 1). commit: see git log.
Regression tests: unit `tests/test_confidence_immutable.py`; scenario R04 (+ INV-01 on every turn of every scenario).
Verified: 3/3 green, 0 invariant violations.

---

## Disputed assertions
(none yet)

## capability-limit? items
(none yet)
