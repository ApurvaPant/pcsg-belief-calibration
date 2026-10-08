# Known issues

## KI-1 — Read path fabricates attribute values (open, do not fix yet)

**Symptom.** The assistant replied *"You've been enjoying momo for about five
years"* when the `preference/food_likes = momo` node has **no `duration`
attribute**. The generation step is inventing attribute values that are not in
the graph.

**Where.** The write path is correct — `_profile_summary` (`pipeline.py`) only
injects attributes that actually exist on active nodes, and never a duration
for momo. The fabrication happens at **reply generation**: the chat model
elaborates beyond the injected profile.

**Likely fix (later).** Constrain the chat system prompt to answer only from the
provided profile and to say it doesn't know when an attribute is absent; and/or
pass the profile as structured facts with an explicit "do not infer values"
instruction.

**Regression guard now.** `tests/test_no_fabrication.py` asserts the retrieval
profile for a node without a `duration` never contains one, and the (mock)
reply to "how long have I liked momo?" contains no fabricated duration figure.
Status: logged; not fixed.
