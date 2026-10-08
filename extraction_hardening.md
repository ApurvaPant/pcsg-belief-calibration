# extraction_hardening.md

Paste everything below the line into Claude Code.

---

The knowledge graph is producing incorrect data. The bugs below are all in the extraction contract and the pipeline code, not in model capability — do not respond to any of them by swapping models.

Work through the parts in order. Stop after each part, report what changed, and wait for me before continuing.

## Observed bugs

1. **Subject attribution missing.** "my dog name is herald" wrote `identity/name = Herald` and superseded the user's own name. Every assertion is assumed to be about the user.
2. **Questions create nodes.** "what is my girlfriend name" (salience 0.95) created a node literally called `girlfriend name`. "what is my favorite food" (0.95) reinforced a preference. "what is my name" scored 1.00.
3. **Invented edges.** "i love momo as a food" produced an edge `momo --related_to--> hailey`. Nothing in that sentence mentions hailey.
4. **Duplicate entities.** The graph holds both `hailey` and `girlfriend hailey` as separate nodes for the same person.
5. **Attributes still not firing.** "6 months", answering the assistant's question "how long have you and Hailey been together?", produced `CREATED: RELATIONSHIP` instead of an attribute on the hailey node.
6. **Sentence-length values.** A node exists whose value is `grape wine with fermentation over 1 year`.

## PART 0 — Reset the database

The current graph is corrupted: a polluted identity chain (Apurva → Herald → Apurva → Herald), phantom nodes from questions, duplicate entities, invented edges. Do not attempt to repair it.

Add `scripts/reset_db.py` that drops and recreates all tables, and run it. Everything below is verified against a clean graph.

## PART 1 — Questions never produce assertions

Fix in tier 0, before any LLM call is made.

1. Classify the message: it is a **retrieval query** if it is interrogative (leading wh-word, auxiliary inversion, or ends with `?`) AND contains no independent declarative clause.
2. Retrieval queries get salience 0 and skip the write pipeline entirely. No classifier call, no extractor call, no gate_log entry beyond the decision.
3. Badge them `RETRIEVAL_QUERY` in the UI, distinct from `GATED_OUT`, so it is visible that the read path ran instead.
4. Mixed messages ("my name is apurva, what's yours?") extract only the declarative clause.
5. Add tests: "what is my name", "who is my girlfriend", "do you know what I like?" all produce zero assertions.

## PART 2 — Subject attribution

Add a `subject` field to the extraction schema. Every assertion must declare whose it is.

```
subject: "self"                              — about the user
subject: {dimension, key, value}             — about a related entity
```

Grammar rule for the extraction prompt, with these worked examples:

> If a possessive chain contains an intermediate noun between the first-person pronoun and the attribute noun, the subject is that intermediate entity, not the user.

| message | subject | result |
|---|---|---|
| "my name is apurva" | self | `identity/name = apurva` |
| "my dog name is herald" | `relationship/pet = dog` | attribute `name = herald` on that node |
| "my brother is a doctor" | `relationship/brother` | attribute `occupation = doctor` |
| "my girlfriend is hailey" | self | `relationship/girlfriend = hailey` (naming the relation itself) |
| "hailey's birthday is in may" | `relationship/girlfriend = hailey` | attribute `birthday = may` |

**Hard guard on identity.** `identity/*` may be written ONLY when `subject == "self"` AND the evidence quote matches a direct self-reference pattern: `my name is X`, `i'm called X`, `call me X`, `i am X`, `i'm X`. Reject any identity write whose quote contains a possessive noun phrase between the pronoun and the attribute (`my <noun> name is`). Log `reason_code = subject_mismatch`.

**Entity attributes belong to entities.** When subject is an entity, resolve it to an existing node first; create it only if absent, then attach the attribute. Never write an entity's attribute onto the user.

## PART 3 — Edges only from explicit stated relations

Delete whatever similarity, recency or co-occurrence linking currently exists. It is what produced `momo --related_to--> hailey`.

1. An edge may be created ONLY when the source message explicitly names both endpoints in a relational construction: "X is part of Y", "I use X for Y", "X and Y together", "X for my Y".
2. Never create an edge from embedding proximity, recency, or same-session co-occurrence.
3. Every edge row stores the quote that justified it. If no quote names both endpoints, the edge is not created — log `edge_unjustified`.
4. Both endpoints must already exist as nodes. Never create an endpoint implicitly from a relation.
5. Test: "i love momo as a food" creates a preference node and **zero** edges.

## PART 4 — Entity deduplication

1. Normalise values by stripping the relation word when it duplicates the key: `key=girlfriend, value="girlfriend hailey"` → `value="hailey"`.
2. Before creating a node, check existing nodes in the same dimension for value overlap after normalisation. If found, REFINE rather than create.
3. Add a merge endpoint `POST /nodes/{id}/merge_into/{target_id}` that moves evidence and attributes across and marks the source merged, so duplicates can be collapsed manually.

## PART 5 — Diagnose the attribute path

"6 months" should have become an attribute. Before patching anything, check each stage and report which one fails:

- (a) is a `pending_slot` opened when the assistant asks about an attribute?
- (b) is the open slot passed to the extractor on the next message?
- (c) does the extractor emit `target_node`?
- (d) does the pipeline route `target_node` to the attributes table rather than creating a node?

Report (a) through (d) before writing any fix.

## PART 6 — Structured outputs

Make bad writes impossible at the schema level rather than catching them downstream.

1. Use the provider's structured-output / JSON-schema mode for both the classifier and the extractor.
2. Constrain in the schema itself: `dimension` is an enum of the ten allowed dimensions; `operation` is an enum of ADD/UPDATE/END/REFINE; `subject` is `"self"` or an object; `key` matches `^[a-z][a-z0-9_]*$` and must not equal `general`; `value` has a maximum length.
3. Hard validation before any write: reject values longer than 4 words or containing a preposition phrase (with / over / from / for / about / that). Attempt decomposition into value + attributes; if that fails, log `validation_failed` and drop.
4. Any schema violation is logged with the raw model output so we can see what it tried to emit.

## PART 7 — Split the extraction call

One call is currently making six decisions (subject, dimension, key, value, operation, confidence). Split it into two:

1. **Frame call** (cheap model): given the message plus 4 turns of context plus any open pending slot, return `{subject, dimension, operation, target_node?}`.
2. **Fill call** (stronger model): given the frame, return `{key, value, attributes[], explicitness, quote, confidence}`.

Log both calls separately in `gate_log` with their own token counts — we need per-stage cost for the cost-curve experiments later.

## PART 8 — Negative examples in the prompt

The prompt shows what to extract but not what to refuse. Add explicit null-output examples:

- a question → `[]`
- "i am doing great" → `[]` (transient mood)
- "as of now no nothing such i am working with" → `[]` (non-statement)
- "thanks, that helps" → `[]`
- "my dog name is herald" → assertion with subject = the dog, NOT identity

## PART 9 — Extraction log for future distillation

Add an `extraction_log` table: message_id, context_window (json), frame_output (json), fill_output (json), model, prompt_version, accepted (bool), reason_code, created_at.

Write a row for every extraction attempt including rejected ones. This becomes the training set if we later distil a small extractor, and it costs nothing now.

## PART 10 — Regression test set

Create `tests/extraction_cases.json` with these 20 labelled cases and a test that runs them against the mock provider:

```
"my name is apurva"                      -> identity/name = apurva, subject self
"my dog name is herald"                  -> attribute name=herald on pet/dog
"what is my name"                        -> [] (retrieval query)
"what is my girlfriend name"             -> [] (retrieval query)
"my girlfriend is hailey"                -> relationship/girlfriend = hailey
"for last 6 months"                      -> attribute duration=6 months (with slot open)
"i am doing great"                       -> []
"as of now no nothing such i am working with" -> []
"i drank beer"                           -> []
"i love momo as a food"                  -> preference/food_likes = momo, 0 edges
"i hate momos"                           -> preference/food_dislikes = momos
"i started drinking wine"                -> habit/drinking_wine
"my favorite wine is grape wine with fermentation over 1 year"
                                         -> preference value "grape wine"
                                            + attribute fermentation_duration=1 years
"i stopped doing machine learning"       -> END on the ml node
"i broke up with hailey"                 -> END on relationship/girlfriend
"i have been coding in python for 3 years" -> skill/python + years_experience=3
"machine learning is part of my cs degree" -> edge ml --part_of--> cs
"my brother is a doctor"                 -> attribute occupation on relationship/brother
"my name is ujjwal"                      -> identity supersedes, history chain intact
"thanks that helps"                      -> []
```

## Verification

After all parts, reset the database and run the 20 cases end to end through the real provider. Report a table: case, expected, actual, pass/fail.

## If time is short

Order: PART 0, PART 1, PART 2, PART 3. Those four fix the visible damage. Parts 5–10 follow.