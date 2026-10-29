# extractor_improvement_today.md

Paste everything below the line into coding agent. Copy `extractor_eval_cases.json` into `backend/tests/` first.

---

Goal for today: make the extractor measurably good, then freeze it so tomorrow's benchmark runs on a fixed version. Every change today must be justified by a score, not by eyeballing the graph.

Work through the steps in order. Stop after each step and report the numbers before continuing.

## STEP 1 — Evaluation harness (do this before touching any prompt)

The file `tests/extractor_eval_cases.json` has 45 labelled cases across 15 categories, split into `dev` and `test`.

Build `scripts/eval_extractor.py`:

1. For each case: reset to a fresh in-memory DB, run every `history` message through the FULL pipeline in order, then inject `assistant_last` as the preceding assistant turn (so pending slots can open), then run `message`.
2. Capture what the pipeline actually stored or changed for `message` — use the `extraction_log` and `events` rows, not the UI.
3. Score each case:
   - **Negative cases** (`expect: []`): pass only if zero assertions were written.
   - **Positive cases**: match each expected assertion against the output. Field-level checks: subject, dimension, key (any of `key_any`), value (normalised, or any of `value_any`), operation (or any of `operation_any`), attribute name/value/unit, explicitness where given.
   - **`graph_after` checks**: verify `unchanged`, `superseded`, `still_active`, `ended_not_deleted`, `must_not_exist`, `active_count`, `node_count_unchanged`, `no_value_longer_than_words`.
   - A case passes only if every expected assertion and every graph check passes.
4. Report:
   - overall pass rate, separately for `dev` and `test`
   - pass rate per category
   - **precision / recall / F1** at the assertion level (an assertion counts as correct only if subject + dimension + key + value + operation all match)
   - **field-level accuracy**: subject, dimension, key, value, operation — separately. This tells us WHICH decision is failing.
   - **false-write rate** on negative cases
   - tokens and cost per message, broken down by stage (tier 1, frame, fill)
5. Save results to `eval_results/<timestamp>_<prompt_version>.json` and print a compact table. Every run is kept so we can compare.
6. Add `--split dev|test|all` and `--case <id>` flags, and `--runs 3` to repeat the dev set three times (LLM outputs vary; we need to know how stable the score is).

**Rule: while tuning prompts, run `--split dev` only. Do not look at test-case failures until STEP 6.** Otherwise we overfit our prompts to the test set and tomorrow's numbers mean nothing.

Run the baseline on the current system with `--split dev --runs 3` and report the numbers before changing anything.

## STEP 2 — Deterministic rules for the decisions rules can make

Some decisions are closed-vocabulary and should never depend on a model. Move them into tier 0, before any LLM call, and pass the result to the frame/fill calls as a **hard constraint**, not a hint:

1. **END detection.** Match: stopped, quit, no longer, not anymore, don't … anymore, gave up, broke up with, split up, left (my job / the company), dropped out, moved away from, finished, graduated from, sold, cancelled, used to. If matched → `operation = END`. The model may not override it.
2. **Retrieval queries.** Already in place; keep it. Mixed messages must still pass their declarative clause through (case `mixed-02`).
3. **Subject from possessive chains.** Regex `my <noun> ('s)? (name|age|job|…) is` → subject is the entity named by `<noun>`, never `self`. `<noun>`'s own name ("my girlfriend is hailey") stays `self` + relationship.
4. **Supersession phrasing.** "moved to", "switched to", "changed to", "now", "anymore" set a `replacement_hint` passed to the model.

Log every rule that fired in `extraction_log.rules_fired`.

Re-run `--split dev --runs 3`. Report the delta per category.

## STEP 3 — Restructure the two calls

Currently the cheap frame model makes the structural decisions (subject, dimension, operation) and the stronger fill model only extracts values. That is backwards: the structural decisions are the hard ones.

Change the split:

- **Call A — span extraction (cheap model):** given the message, the 4-turn context, any open pending slot, and the tier-0 rule results, return a list of candidate spans — the exact substring that carries each assertion. No classification.
- **Call B — structuring (stronger model):** given each span, the context, the tier-0 constraints, and **the list of the user's existing active nodes for the relevant dimensions** (key + value only, max 30), return the full structured assertion: subject, dimension, key, value, operation, attributes, explicitness, confidence.

Passing existing nodes into call B is the most important part of this step. Without it the model cannot know whether "i stopped coding" refers to `habit/coding` or `interest/coding`, whether "hailey" is already in the graph, or whether a new key duplicates an existing one.

If call A returns no spans, call B is skipped — that is where most of the cost saving comes from.

Report the token cost per message before and after, alongside the dev-set scores.

## STEP 4 — Prompt quality

1. **Ontology card.** A compact block in call B's prompt listing every dimension with its cardinality, its allowed attribute names, and 2 example keys. The model should never have to guess what `habit` vs `interest` means. Use this boundary:
   - `interest` = topics the user likes thinking or reading about
   - `habit` = activities the user regularly *does*
   - `skill` = things the user *can* do
   - `goal` = things the user *wants* to achieve
2. **Few-shot examples, chosen by failure.** Take each category that failed on dev and add ONE example targeting it. Use different wording from the eval cases — never copy an eval message into the prompt.
3. **Reasoning field.** Add a short `rationale` string to call B's schema, placed BEFORE the decision fields, limited to 25 words. This measurably improves structured classification. Log it; do not show it in the UI.
4. **Normalisation in code, not in the prompt.** Lowercase, strip articles and leading verbs ("doing", "eating", "is to"), singularise where safe, strip the key word from the value. Apply after the model returns — do not rely on the model for it.

Re-run dev, report.

## STEP 5 — Resolution and update policy

The extractor can be correct and the graph still wrong if resolution misfires. Check these against the dev cases:

1. Resolution matches on exact `(dimension, key)` first, then normalised value, then embedding similarity **≥ 0.92 within the same key only**. Never across keys.
2. Every REINFORCE / END / supersede logs the matched node id, label and similarity score.
3. END with no match → `no_matching_node`, no write. END with multiple matches → `ambiguous_match`, no write.
4. An assertion with no value (e.g. "i'm in a relationship") creates a node with `value = null` and opens a pending slot for the value — no blank-labelled nodes in the graph.

Re-run dev, report.

## STEP 6 — Held-out evaluation and freeze

Only once the dev score has stopped improving:

1. Run `--split test --runs 3`. Report overall, per-category, P/R/F1, field-level accuracy, false-write rate, and mean ± spread across the 3 runs.
2. If test is more than ~15 points below dev, say so explicitly — it means the prompts are overfit to the dev cases.
3. **Freeze**: write `EXTRACTOR_VERSION.md` containing the prompt_version hash, exact model strings for every role, temperature, all thresholds, and the final dev and test scores. Commit with a git tag `extractor-v1`.
4. From this point no prompt or pipeline change is allowed before tomorrow's benchmark without bumping the version and re-running this evaluation.

## Constraints

- Do not change model strings today. If after STEP 5 a specific category is still failing and you believe it is a model capability limit, report it with evidence (the raw outputs) and stop — I will decide.
- Temperature 0 for calls A and B during evaluation.
- Never put an eval-set message into a prompt.
- Keep all existing tests passing; add the eval harness as a separate script, not as a pytest test (it hits the network).