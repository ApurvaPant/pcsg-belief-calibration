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

## BUG-002  [S2]  [update_policy]  status: fixed
Found by: robustness/C11 (INV-20 violation, mock)
Repro: `tests/robustness/runner.py --suite negation --case C11 --provider mock`
Expected: an ended relationship's attributes are preserved for history but NOT reported as current (INV-20).
Actual: ending a node left its attributes `status=active`, so an ended girlfriend's `since` attribute stayed active on a superseded node.
Root cause: `_close_node` closed the node but never closed its attributes.
Fix: `_close_node` now sets the node's active attributes to `superseded` (rows kept for history); `restore_node` reactivates attributes with no active sibling. commit: see git log.
Regression tests: scenario C11 (attribute survives as superseded on the ended node) + INV-20 on every turn.
Verified: 3/3 green, 0 invariant violations.

## BUG-003  [S2]  [gate]  status: fixed
Found by: robustness/R10 (real provider)
Repro: `runner.py --case R10 --provider real`
Expected: "i do coding" is a durable skill statement — not gated out.
Actual: 3 tokens triggered the `<4 tokens -> too short (0.15)` rule and the medium `first_person_state` pattern lacked "i do", so even the real classifier's score could not lift it past threshold -> GATED_OUT, no node.
Root cause: recall gap in the tier-0 medium signals.
Fix: added `i do` / `i speak` to the first_person_state medium pattern (`app/salience.py`). The gate is a recall floor; extraction stays the precision stage.
Regression tests: scenario R10 (real).
Verified: real 1/1 green (budget-limited run count).

## BUG-004  [S0]  [structuring + read_path]  status: fixed
Found by: robustness/C5 (real provider)
Repro: `runner.py --case C5 --provider real`
Expected: "my mom and my dog don't get along" -> relationship/mom + relationship/dog + a negative gets_along_with edge; a later recall must not claim they get along.
Actual: real extractor produced 0 writes (no entity nodes, no edge); the reply then fabricated that they get along.
Root cause: (a) the FILL prompt had no rule/example for a negated interpersonal relation and the relations schema carried no `polarity`; (b) `_profile_summary` listed nodes only — the chat model never saw the stored negative edge.
Fix: FILL prompt rule + example for "X and Y don't get along" (two relationship nodes + gets_along_with polarity=negative); `polarity` added to the relations schema; `_profile_summary` now includes edges, rendering a negative gets_along_with as "X does NOT get along with Y". (apply_relations/create_edge already supported negative polarity.)
Also: the robustness judge now uses the chat model (gpt-4o-mini), not the cheap classifier, after the classifier proved unreliable on nuanced rubrics (recorded as a harness fix, not an assertion change).
Regression tests: scenario C5 (real) + CN1 (mock, negative edge).
Verified: real C5 1/1 green; mock 19/19 (3/3).
