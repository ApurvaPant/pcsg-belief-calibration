# kg_depth_upgrade.md

Paste everything below the line into coding agent.

---

The knowledge graph is the core component of this project and it currently stores a flat list of facts. It needs depth. Work through the parts in order and stop after each so I can verify before you continue.

## Bugs that motivate this work

Observed in testing, all traceable to the same few root causes:

1. **Attribute lost.** User: "my girlfriend is hailey" → node created. Assistant asked "how long have you been together?" User: "for last 6 months" → nothing stored. In a later chat the assistant recalled Hailey but had no duration. "6 months" is an attribute of the hailey node and there is nowhere to put it.
2. **Duplicate concept instead of refinement.** "i started drinking wine" → `habit/drinking_wine`. Then "my favorite wine is grape wine with fermentation over 1 year" → a brand-new isolated preference node **whose value is the entire sentence**, unlinked to the wine habit.
3. **END silently ignored.** "i broke up with hailey" → badge read `IGNORED: RELATIONSHIP` with no reason. The relationship node stayed active.
4. **Noise scoring high.** "i am doing greatr" 0.75, "as of now no nothing such i am working with" 0.75. Transient states and non-statements are being treated as durable facts.

What already works and must NOT be changed: supersession on SINGLE slots (apurva → ujjwal → apurva produced a correct history chain), and END on an interest ("i stopped doing machine learning" → `ENDED: INTEREST`). The END path functions; its vocabulary and failure reporting do not.

## PART 0 — Failure visibility (do this first; ~15 minutes, and it tells us what is actually breaking)

Every time the pipeline declines to act on an extracted assertion, record why.

1. Add `reason_code` to the events table and to the chat UI badge. Allowed values: `no_matching_node`, `ambiguous_match`, `operation_unsupported`, `validation_failed`, `attribute_orphaned`, `key_unresolved`, `below_threshold`.
2. The badge becomes `IGNORED: RELATIONSHIP (no_matching_node)` rather than a bare `IGNORED`.
3. Log the full extractor JSON alongside the reason code so we can see what the model actually returned.

Re-run "i broke up with hailey" after this and tell me the reason code before doing anything else.

## PART 1 — Node attributes

Add an `attributes` table. Attributes are qualifiers on a node and carry the same evidence and temporal discipline as nodes.

```
attributes
  id
  node_id                FK to nodes
  name                   snake_case, from the allowed set below
  value                  normalised text or number
  unit                   nullable: months, years, times_per_week, ...
  confidence_raw         float, NEVER modified after insert
  confidence_calibrated  float, nullable
  valid_from, valid_to
  status                 active | superseded
  previous_attribute_id  nullable
  created_at, updated_at
```

Evidence must be able to reference an attribute: add nullable `attribute_id` to `evidence`, make `node_id` nullable, and constrain each row to reference exactly one of the two.

Attributes supersede like SINGLE nodes: a new value for the same `(node_id, name)` closes the old one and links via `previous_attribute_id`. Attribute changes appear in the inspector history chain.

Allowed attribute names per dimension — extend the ontology module and reject anything outside the set rather than storing free-form keys:

| dimension | attributes |
|---|---|
| relationship | duration, since, relation_type, status |
| skill | proficiency, years_experience, learning_status |
| habit | frequency, duration, time_of_day, status |
| goal | target_date, progress, priority, status |
| project | role, status, started, tech_stack |
| interest | intensity, since |
| preference | strength, context, since, plus type-specific qualifiers (e.g. fermentation_duration) |
| identity | none — the value is the whole assertion |

## PART 2 — Value decomposition (fixes half of bug 2)

A node value must be a short noun phrase. Never a sentence.

1. When a statement contains a value plus qualifiers, split it:
   - "grape wine with fermentation over 1 year" → value `grape wine`, attribute `fermentation_duration = 1`, unit `years`
   - "python skills from 3 years of work" → value `python`, attribute `years_experience = 3`
2. Hard validation before any node write: reject a value longer than 4 words or containing a preposition phrase (with / over / from / for / about / that). Send it back through decomposition; if that fails, log `validation_failed` and drop it.
3. Put the same rule in the extraction prompt with worked examples so most cases never reach validation.

## PART 3 — REFINE operation (fixes the other half of bug 2)

Add a fourth operation alongside ADD / UPDATE / END. **REFINE** means the user is adding detail about something already in the graph, not asserting something new.

1. Before creating any node, the resolver checks whether an existing node shares the head noun or is semantically close (embedding similarity above `resolve_similarity`). If so, prefer REFINE over ADD.
2. REFINE either writes attributes onto the existing node, or creates a closely related node **and links it** — never an orphan duplicate.
   - "i started drinking wine" → `habit/drinking_wine`
   - "my favorite wine is grape wine" → `preference/favourite_wine = grape wine`, linked to `habit/drinking_wine` via `related_to`
3. Never create an isolated node whose value duplicates a concept already present in the graph.

## PART 4 — Conversational context for extraction (fixes bug 1)

The extractor currently sees one message with no context, so elliptical answers are uninterpretable.

1. Pass the **last 4 turns** (user and assistant alternating), clearly delimited, with the target message marked. Use context ONLY to resolve references — pronouns, ellipsis, short answers — and extract assertions only from the target message.
2. Worked examples for elliptical answers:
   - context: assistant asked "how long have you and Hailey been together?" / target: "for last 6 months" → attribute `duration = 6`, unit `months`, on the node named in context
   - context: assistant asked "what are you working on?" / target: "mostly the graph stuff" → project assertion, not a bare interest
3. Extractor output gains an optional `target_node` field `{dimension, key, value}` identifying an existing node the assertion attaches to. When present, resolve it and write an attribute instead of creating a node.
4. If `target_node` cannot be resolved, log `attribute_orphaned` and drop it. Never invent a node to hang an attribute on.

## PART 5 — END vocabulary and semantics (fixes bug 3)

1. Give the extractor explicit END synonyms beyond "stopped": broke up with, split up, we're no longer together, left my job, quit, dropped out, moved away from, finished, graduated from, sold, cancelled, gave up on.
2. END matches on full node identity — `(user_id, dimension, key, value_normalized)` for MULTI, `(user_id, dimension, key)` for SINGLE. Never key alone. No match → log `no_matching_node`, take no action. Multiple matches → log `ambiguous_match`, close none.
3. END sets `valid_to`, `status = superseded`, `status_detail = ended`, emits `node_ended`. The node stays in history, visible under "Show history". Never deleted.
4. For relationships, ending is not deletion — "i broke up with hailey" must leave the hailey node in the history chain with its attributes intact.

## PART 6 — Slot-filling from assistant questions

When the assistant asks about a specific attribute, record the intent so the next message maps onto it deterministically.

1. Add `pending_slots`: id, session_id, node_id, attribute_name, asked_at_message_id, status (open | filled | expired), created_at.
2. After generating a reply, classify cheaply: did this reply ask about a specific attribute of a known node? If so, open a slot.
3. On the next user message, pass any open slot to the extractor as a strong hint.
4. Mark filled when an attribute lands on it; expire after 3 turns.

Keep this general — the same mechanism will later drive proactive re-validation ("you mentioned learning Rust six months ago — still going?").

## PART 7 — Retrieval must surface attributes

The read path currently injects node values only. Attributes must travel with their node:

```
relationship.girlfriend = hailey  (duration: 6 months, conf 0.82) [ENDED 2026-09-15]
skill.python = python  (proficiency: intermediate, years_experience: 3, conf 0.74)
preference.favourite_wine = grape wine  (fermentation_duration: 1 year, conf 0.80)
```

Send active state only by default. Include ended and superseded items when the question is explicitly historical ("what did I use to...", "have I ever...", "who was my...").

## PART 8 — Inter-node edges

1. Relation types: `part_of`, `requires`, `blocks`, `related_to`, `works_on`, `involves`.
2. Extraction may emit an optional `relations` array `[{from_value, relation, to_value}]`.
3. Create an edge only when **both endpoints already exist as nodes**. Never create endpoints implicitly from a relation. A wrong edge is worse than a missing one.
4. Render distinctly from hub edges — solid, slightly darker, relation type on hover.

## PART 9 — Salience fixes (fixes bug 4)

Scores are still clustered at 0.65/0.75/0.80 and noise scores high.

1. Switch tier-1 output to an integer 0–100, then divide. Models spread integers; they snap decimals to round values. We need that spread for the threshold sweep later.
2. Redefine scoring around durability: would this still be useful to know in a week?
   - HIGH: stated facts about the user, changes, corrections, preferences, plans, answers to a pending slot question.
   - LOW: transient mood ("i am doing great"), non-statements ("as of now no nothing such i am working with"), one-off actions with no lasting implication ("i drank beer"), pleasantries, questions to the assistant, meta-instructions.
3. If an open pending slot exists, apply a salience floor of 60 — the message is probably answering our question.
4. Log the raw integer alongside the parsed float in `gate_log`.

## Verification sequence

Fresh chat, exactly this:

1. `my girlfriend is hailey` → relationship node
2. assistant asks about duration → pending slot opens
3. `for last 6 months` → attribute `duration = 6 months` on the hailey node, NOT a new node
4. `i am doing great` → low salience, gated out
5. `i started drinking wine` → `habit/drinking_wine`
6. `my favorite wine is grape wine with fermentation over 1 year` → `preference/favourite_wine = grape wine` + attribute `fermentation_duration = 1 years` + edge to the wine habit. No node whose value is a sentence.
7. `i have been coding in python for 3 years` → skill node with `years_experience = 3`
8. `i broke up with hailey` → relationship node ended, still visible under Show history with its duration attribute intact
9. `machine learning is part of my cs degree` → edge `machine learning --part_of--> cs`, both nodes pre-existing

Then open a NEW chat and ask "who is my girlfriend and how long were we together?" — both facts must come back, with the relationship correctly reported as ended.

Report each step before continuing.

## If time is short

Priority order: PART 0 (reason codes), PART 2 (value decomposition), PART 1 (attributes), PART 4 (context), PART 9 (salience). Parts 3, 5, 6, 7 and 8 follow afterwards.