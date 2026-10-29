# PCSG — modelling fix + multi-session + graph rendering

Paste everything below the line into coding agent.

---

The current build has three modelling bugs, no multi-session support, and a weak graph rendering. Fix them in the order below. Stop after each part and show me the result.

Observed bugs to fix:
- "i love machine learning" then "i love jogging" then "i love momos" produced ONE node, each superseding the last. They are unrelated preferences and must coexist.
- Every assertion is landing in key `general`. Keys are not being derived from content.
- "i stopped doing jogging" scored salience 0.10 and was gated out. Negation is a state change and must always pass the gate.
- Values keep raw phrasing ("is to become a pilot" instead of "become a pilot").

## PART 1 — Ontology with cardinality

Create `backend/app/ontology.py` defining the slot space. Every extracted assertion must map to a `(dimension, key)` pair from this table, and cardinality decides whether a new value supersedes the old one or coexists with it.

| dimension | example keys | cardinality |
|---|---|---|
| identity | name, age_range, location, occupation, employer | SINGLE |
| preference | diet, communication_tone, response_length, work_style | SINGLE |
| preference | food_likes, food_dislikes, music_genre, tool_choice | MULTI |
| interest | topic | MULTI |
| goal | career, learning, fitness, financial, personal | MULTI |
| skill | (key = the skill itself, e.g. python, tamil) | MULTI |
| project | (key = project name) | MULTI |
| habit | (key = the habit, e.g. jogging, morning_reading) | MULTI |
| belief | (key = the topic the belief is about) | MULTI |
| relationship | (key = person name or role) | MULTI |
| communication_style | formality, verbosity, humour | SINGLE |

Rules:
- **SINGLE** slots hold exactly one active value. A new value supersedes the old one.
- **MULTI** slots hold many active values. A new value is a NEW node. It only supersedes if the extractor explicitly marks it as replacing a specific existing value.
- Keys are snake_case, derived from the content, never `general`. If the extractor cannot determine a key, it must return `null` and the assertion is DROPPED, not stored under a fallback.
- Values are normalised: lowercase, leading verbs and articles stripped, no trailing punctuation. "is to become a pilot" → "become a pilot". "doing machine learning" → "machine learning".

Add a `status_detail` field on nodes for MULTI slots that can end: `active | ended | achieved | abandoned` (a habit stops, a goal is achieved, a project finishes).

## PART 2 — Extraction emits operations, not just values

Change the tier-2 extraction output. Each assertion is now:

```json
{
  "dimension": "habit",
  "key": "jogging",
  "value": "jogging",
  "operation": "END",
  "replaces_value": null,
  "polarity": "negate",
  "explicitness": "explicit",
  "quote": "i stopped doing jogging",
  "confidence": 0.9
}
```

`operation` is one of:
- **ADD** — a new assertion. For MULTI slots creates a new node; for SINGLE slots supersedes the current value.
- **UPDATE** — reinforces or refines an existing assertion (same key and value).
- **END** — the user stopped, quit, abandoned, or no longer holds this. Closes the matching node: set `valid_to`, `status = superseded`, `status_detail = ended`. Creates NO new node.

`replaces_value` is set only when the user explicitly swaps one value for another ("I switched from Python to Rust") — then the named old value is ended and the new one added.

Give the extraction prompt 6 worked examples covering: a SINGLE slot change, two unrelated MULTI additions, an END, an explicit replacement, and a message with no assertions at all.

## PART 3 — Update policy rewrite

Rewrite `update.py` around node identity:

- MULTI slot identity: `(user_id, dimension, key, value_normalized)`
- SINGLE slot identity: `(user_id, dimension, key)`

Then:
- Identity already exists and operation is ADD/UPDATE → reinforce via Noisy-OR, extend validity, add evidence, emit `confidence_changed`.
- SINGLE slot, identity exists, different value → supersede: close old, create new with `previous_node_id`, emit `conflict_detected` + `node_superseded`.
- MULTI slot, new value → create a new node. **Do not touch sibling nodes under the same key.**
- Operation END → close the matching node only. If no match, log and ignore.
- `replaces_value` set → end the named node, add the new one, link `previous_node_id`.

Write tests covering exactly the failing sequence above: three unrelated preference additions must produce three active nodes; the END must close one node and leave the others untouched.

## PART 4 — Gate fixes

Tier 0 must pass, with high salience, any message containing:
- self-identification: "my name is", "i'm called", "call me", "i am a/an"
- negation and cessation: "stopped", "quit", "no longer", "not anymore", "gave up", "used to"
- explicit replacement: "switched to", "moved from", "instead of", "changed to"
- preference verbs, temporal markers, first-person statements with a complement

Tier 0 now ROUTES rather than vetoes: it should reject only pure pleasantries, questions addressed to the assistant, and messages under four tokens. Target: reject 40-60% of turns with near-zero false negatives.

## PART 5 — Multi-session

Restructure so memory belongs to the **user**, not the session.

- Add a `users` table (id, label, created_at). Add `user_id` to `nodes`.
- `sessions` gets `user_id` and `title`. `evidence` keeps `session_id` and `message_id` so every assertion knows which conversation it came from.
- A new chat opens a new session against the SAME user graph. State carries over.
- API: `GET /users/{id}/sessions`, `POST /users/{id}/sessions`, `GET /users/{id}/graph` (graph is user-scoped, not session-scoped).
- Frontend: a left sidebar listing chats, a "New chat" button, and the graph pane always showing the full user graph. When a node's evidence comes from a different session, the inspector labels which conversation it came from.
- Add a session-end consolidation hook (`POST /sessions/{id}/close`) that stubs out for now but is where cross-session merging will later live.

## PART 6 — Graph rendering (Obsidian-style)

Replace the current layout with a proper force-directed knowledge-graph view.

Structure — three tiers, which is what creates visual clustering:
```
YOU (centre, large)
 └── dimension hubs (preference, goal, skill, habit, ...) — medium, coloured
      └── value nodes (machine learning, momos, jogging) — small, coloured by parent
```
Only render a dimension hub if it has at least one node.

Layout: use Cytoscape's **fcose** layout (`cytoscape-fcose`) with these behaviours:
- Physics-based, with node repulsion and edge length tuned so clusters separate visibly.
- **Incremental**: when a new node arrives, run layout with `randomize: false` and `animate: true` so existing nodes settle rather than the whole graph jumping.

Visual encoding:
- Node size scales with confidence (min 12px, max 34px) — make sure this is actually bound to the value, currently everything renders identically.
- Colour by dimension, consistent with the legend.
- Superseded nodes: 35% opacity, dashed border, and hidden by default behind a "Show history" toggle.
- Edge labels only on supersession links ("superseded").

Interaction:
- **Hover a node** → highlight it and its direct neighbours at full opacity, dim everything else to 15%. This is the Obsidian effect and the single biggest visual improvement.
- Click → inspector (already built).
- Drag to reposition, scroll to zoom, drag background to pan.
- Labels hidden below a zoom threshold when the graph exceeds ~40 nodes, so it doesn't turn into a wall of text.
- A search box that filters and focuses matching nodes.

Dark theme to match the current UI. Keep the legend.

## PART 7 — Fix the backend error

The UI showed "Could not reach the backend." Add a health check on the frontend that surfaces the actual error (connection refused vs 500 vs CORS) rather than a generic message, and make sure CORS allows the Vite dev origin.

## Verification

After all parts, run this exact sequence in a fresh chat and confirm:

1. "my name is daksh" → identity/name node created, high salience, NOT gated out
2. "i love machine learning" → interest/topic = machine learning
3. "i love jogging" → habit/jogging OR interest/topic = jogging, and machine learning STILL ACTIVE
4. "i love momos" → preference/food_likes = momos, all three still active
5. "i stopped doing jogging" → jogging node closed with status_detail = ended, others untouched
6. "i'm vegetarian" then later "i eat meat now" → SINGLE slot preference/diet supersedes, history chain visible

Screenshot the graph after step 6 and show me.