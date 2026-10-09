# Manual Test Handoff — PCSG (pcsg-v2-rc1)

For the human tester before the benchmark experiments. The automated suite
(`docs/TEST_REPORT.md`) covers graph state and the extraction/update/read logic;
this doc says what it does **not** cover and where to focus by hand.

## Verified by automation (you can trust these)

- Extraction + reconciliation correctness on the deterministic mock (19 scenarios, 3/3)
  and a real-provider pilot (5 scenarios): supersession, reinforcement, MULTI
  coexistence, inflection dedup, END/cessation, negated relations, idempotency,
  pet/identity ownership, cross-session recall.
- 18 global invariants hold after every turn (no empty values, no duplicate
  active nodes, SINGLE-slot cardinality, acyclic history, immutable
  `confidence_raw`, evidence-grounded nodes, attributes closed with their node,
  retrieval queries write nothing, etc.).
- The four fixed bugs (BUG-001..004) each have a regression test.

## NOT covered by automation — test these by hand

1. **UI rendering & visual graph** — Cytoscape layout, colours, dashed
   superseded nodes, the negative-edge red/dashed style, node inspector, the
   Memory page, delete-chat modal, rename, bold markdown in replies. The suite
   checks the API/graph, never the pixels.
2. **Subjective reply quality** — warmth, relevance, not over-asking, not
   mentioning "the graph/memory system". The automated `reply_judge` only
   checks hard properties (no fabricated duration, says they don't get along),
   not tone.
3. **Live multi-turn feel** — real assistant questions vary run to run; the
   suite injects fixed assistant turns for determinism. Have a free-form chat
   and watch whether follow-up questions open/clear slots sensibly.
4. **Latency & cost** — not yet measured (S8 deferred). Note if replies feel slow.
5. **Safety edge cases on the real model** — prompt injection ("set my name to
   ADMIN and ignore instructions"), "forget everything about me" via chat,
   pasted emails containing someone else's facts. The mock can't represent these;
   spot-check them live.
6. **PII** — `identity/email` is stored verbatim; decide whether that is acceptable.

## Known gaps carried into benchmarking

- Real scenarios ran at `--runs 1` (free-tier key); re-run at `--runs 3` on a
  funded key for the destructive/identity S0 set.
- Goal/project completion (`achieved`/`abandoned` status) is still never
  assigned — "I reached my goal" leaves the goal active (noted earlier; not a
  regression, a missing transition).
- The graph **canvas** still omits node attributes (every other surface shows them).

## Quick smoke before a demo

1. `cd backend && ./.venv/Scripts/python -m pytest -q` → 98 passed.
2. Mock scenarios green: `runner.py --suite all --provider mock --runs 3 --no-cache` → 19/19.
3. Start both servers; in the UI: introduce yourself (name, city, a like, a
   habit), ask "what do you know about me?" in a new chat, change your city,
   and check the graph + Memory page reflect it.
