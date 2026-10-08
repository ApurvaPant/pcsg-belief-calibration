# pcsg_v2_revamp.md

Paste everything below the line into Claude Code. Run `extractor_improvement_today.md` first — this revamp builds on a frozen, scored extractor.

---

# PCSG v2 — calibration-first revamp

The project's research contribution is now fixed. Every part of this revamp serves one of these three questions:

1. **Calibration.** Are the confidence scores stored on user-state assertions calibrated? Which confidence signal is best? Does post-hoc recalibration fix them?
2. **Selective prediction.** If the agent asks instead of asserting when calibrated confidence is low, how much does answer accuracy improve, and at what coverage?
3. **Write-policy cost.** How does accuracy trade against token cost when we sweep the multi-tier salience gate together with a calibration-gated write threshold?

Design principles for every change:
- **Raw signals are immutable.** Every confidence signal is stored once and never edited. Calibrated values are derived, versioned, and can be recomputed.
- **Everything is an experiment.** Runs are config-driven, model strings are pinned, prompts are hashed, and every result is saved to disk with the git SHA.
- **Measure before changing.** No change ships without a before/after number from the eval harness.

Work through the phases in order. **Stop after each phase**, report what was built and the numbers, and wait for me.

---

## PHASE 0 — Prerequisite

`extractor_improvement_today.md` must be complete: eval harness built, dev and test scores recorded, `EXTRACTOR_VERSION.md` written, git tag `extractor-v1`. If it is not done, do that first.

---

## PHASE 1 — Data model

Add these tables. Keep all existing tables and the rule that `confidence_raw` is never modified after insert.

```
confidence_signals      append-only
  id, assertion_type (node|attribute), assertion_id,
  signal (verbalized|self_consistency|logprob|evidence|intensity),
  value (float 0-1), meta_json, created_at

calibrators
  id, version, method (temperature|platt|isotonic), signal,
  fit_run_id, params_json, created_at, active (bool)

assertion_labels
  id, assertion_type, assertion_id, run_id,
  label (0|1), label_source (synthetic|judge|human),
  judge_model (nullable), created_at

runs
  id, name, config_json, git_sha, extractor_version,
  prompt_hashes_json, started_at, finished_at, status,
  total_prompt_tokens, total_completion_tokens, total_cost_usd

qa_results
  id, run_id, benchmark, question_id, question, gold, answer,
  verdict (correct|incorrect|abstained), supporting_assertion_ids,
  confidence_used, tokens, latency_ms
```

Add to `nodes` and `attributes`: `valence` (-1/0/+1, nullable), `intensity` (0-1, nullable), `calibrator_version` (nullable).

`confidence_raw` is defined as the output of the configured **raw combination** (PHASE 2.6), written once at insert. `confidence_calibrated` is written by the active calibrator and may be recomputed.

---

## PHASE 2 — Confidence signals

Compute every signal below for every assertion that passes the gate, and write each to `confidence_signals`. The goal is to *compare* signals, so store all of them even when only one is used.

1. **Verbalized** — the integer 0–100 `confidence` field from the structuring call (Prompt P3), divided by 100.
2. **Self-consistency** — re-run the structuring call k times (default k=5, temperature 0.7) on the same span. Value = fraction of samples that agree with the temperature-0 output on `(subject, dimension, key, value_normalised, operation)`. Runs only on spans that passed the gate, which keeps cost bounded. k lives in config.
3. **Logprob** — if the provider returns token logprobs, the mean logprob of the value tokens, exponentiated. If not available, store null. Nothing may depend on this signal existing.
4. **Evidence** — Noisy-OR over supporting evidence rows, each weighted by its explicitness prior (explicit 0.85, hedged 0.55, inferred 0.40, all from config). Contradicting evidence with weight q multiplies the current value by (1 − q).
5. **Semantic intensity** — for preference, interest and belief assertions only:
   - **valence** −1 / 0 / +1 and **intensity** 0–1, from two sources stored separately: (a) a lexicon in code (love/hate/adore/can't stand ≈ 0.9; like/dislike/enjoy ≈ 0.6; kinda/moderately/sort of/a bit ≈ 0.35; negation and intensifiers handled), and (b) the model's estimate from P3.
   - Store intensity as a `strength` attribute on the node, so retrieval can rank strong preferences above weak ones.
   - Intensity is NOT confidence. "I kinda like pizza" can be fully true. Intensity is recorded as a *feature*; the research question is whether it predicts correctness or persistence (do strongly stated preferences get superseded less often?).
6. **Raw combination** — configurable. Default: `self_consistency × explicitness_prior`. Write the result to `confidence_raw`. Because every signal is stored, the calibration study can compare all of them regardless of which one is the default.

Report: distribution of each signal on the dev eval set (histogram data plus mean/std), and the pairwise correlation between signals.

---

## PHASE 3 — Ground truth

Calibration needs a correctness label for every assertion. Use two sources.

### 3a. Synthetic longitudinal personas (primary — exact labels)

Build `pcsg/datagen/`:

1. **Persona scripts.** Generate 100 personas. Each is a *timeline of events* written in code, not by an LLM: stable facts, value changes on single-valued keys, additions to multi-valued keys, END events, entity facts (pets, family, partner), hedged statements, statements of varying intensity, and noise turns (greetings, questions, transient moods, one-off actions). Target ~30 user turns per persona, so roughly 1,000+ storable assertions in total.
2. **Truth table.** For each persona, the script itself defines the correct user state at every turn index. This table is the ground truth — never the LLM output.
3. **Verbalisation.** Use Prompt P6 to turn each scripted event into a natural user message, with a scripted style (explicit / hedged / indirect). Noise turns come from a fixed list plus P6.
4. **Generation-noise check.** Sample 50 generated messages at random and write them to `datagen/verify_sample.csv` for me to hand-check whether each expresses its scripted event. Report the generator's own error rate. If it exceeds 5%, fix P6 before going further.
5. **Split by persona** into calibration-fit (40%), calibration-test (40%) and dev (20%). Never split by assertion — that leaks a persona's facts across splits.
6. **Labelling.** Replay each persona through the full pipeline. Every stored assertion is matched to the truth table at the turn it was written, using deterministic matching first and Prompt P7 for value equivalence. Label 1 if true at that time, 0 otherwise. An assertion with no counterpart in the script (a hallucination) is labelled 0. Write all labels to `assertion_labels` with `label_source = synthetic`.

### 3b. External benchmarks (answer-level labels)

1. **LongMemEval** — `xiaowu0162/longmemeval-cleaned` on Hugging Face. Start with the 78 knowledge-update questions, then the single-session-preference questions. Ingest each instance's history sessions through the pipeline, then answer the question through the read path.
2. **PersonaMem** — `github.com/bowen-upenn/PersonaMem`. A slice of the preference-evolution question types.
3. Judge every answer with Prompt P5 → `qa_results.verdict`. The answer's confidence is the calibrated confidence of its top supporting assertion. Record the supporting assertion ids.
4. **Judge validation.** Write 100 random judge decisions to `eval/judge_check.csv` for me to hand-label. Report agreement and Cohen's κ. If κ < 0.7, fix P5 before trusting any QA numbers.

Pin dataset versions (commit hash or HF revision) in the run config.

---

## PHASE 4 — Calibration module

Build `pcsg/calibration/`:

1. **Metrics** — implement in numpy and unit-test against `netcal`:
   - ECE with 10 equal-width bins AND 15 equal-mass bins (report both — results can differ)
   - MCE, Brier score, NLL, AUROC (does confidence separate correct from incorrect?)
   - 95% bootstrap confidence intervals (1,000 resamples, resampling *personas* rather than assertions)
2. **Recalibrators** — temperature scaling (on the logit), Platt scaling, isotonic regression. Fit on calibration-fit personas only, evaluate on calibration-test personas only. Save each as a row in `calibrators`; activating one writes `confidence_calibrated` and `calibrator_version` for every assertion.
3. **Outputs** per run, to `results/<run_id>/calibration/`:
   - reliability-diagram data (JSON) and PNGs, before and after each recalibrator
   - a table: signal × recalibrator → ECE / Brier / AUROC with CIs
   - breakdowns by dimension, explicitness, operation and intensity band
4. Never write to `confidence_raw` or `confidence_signals` from this module.

---

## PHASE 5 — Read path: abstention and no fabrication

1. Rebuild the state block for chat (Prompt P4). Mark every item whose calibrated confidence is below `abstain_tau` as `[ASK]`, and ended items as `[ENDED]`.
2. **No-fabrication rule** — known bug: the assistant said "you've been enjoying momo for about five years" when no duration existed. P4 forbids stating any user detail not present in the state block. Add a test: ask about an attribute that does not exist and assert the reply does not invent a value (check with a small judge call).
3. **Risk–coverage curve.** On the LongMemEval/PersonaMem sets, sweep `abstain_tau` from 0 to 1 in steps of 0.05. Report coverage (fraction answered), accuracy on answered questions, and AURC (area under the risk–coverage curve). Compare raw against calibrated confidence as the abstention signal.

---

## PHASE 6 — Write-policy sweep

1. **Grid**: tier-1 salience threshold ∈ {0, 20, 40, 60, 80} × calibration-gated write threshold `write_tau` ∈ {off, 0.5, 0.7, 0.85}. With `write_tau` on, an assertion is written only if its calibrated confidence ≥ `write_tau` — this calibration-gated write is the part that distinguishes the study from existing cost-curve papers (ProMem, MemoryCPT, Personalize-then-Store, Write-Time Gating).
2. **Per cell**, record: prompt and completion tokens per stage, cost in USD, writes per 100 turns, current-state accuracy (synthetic personas), QA accuracy (LongMemEval subset), ECE.
3. **Reference points**: full-context (whole history every turn) and no-memory.
4. **Outputs**: accuracy vs cost with the Pareto frontier marked, points coloured by ECE; and a table of all cells.
5. **Pilot first**: run 5 personas at one cell, extrapolate the cost of the full grid, and print it. If it exceeds `budget_usd` in config, stop and report rather than running.

---

## PHASE 7 — Baselines

Same chat model, same judge, same questions for every system:

1. **No memory.**
2. **Full context** — the whole history in the prompt, truncated at the model's limit; record truncation.
3. **Vector RAG** — embed raw user turns, retrieve top-k (k in config) by cosine similarity.
4. **Mem0** — the open-source package installed with `pip` at a pinned version, never reimplemented. Configure it to use the same model provider.

Baselines have no calibrated per-assertion confidence. For the answer-level calibration comparison, elicit a verbalized 0–100 confidence for each baseline's *answer* with the same prompt wording for every system, and report it as answer-level calibration. Never compare their answer-level numbers directly against PCSG's assertion-level numbers without saying which is which.

---

## PHASE 8 — Experiment runner

1. Configs in `experiments/*.yaml`; run with `python -m pcsg.run experiments/<name>.yaml`.
2. Each run records the git SHA, extractor version, every model string, temperatures, seeds, dataset revisions and prompt hashes into the `runs` table and `results/<run_id>/config.json`.
3. **LLM call cache**: disk cache keyed on (model, prompt hash, temperature, seed, sample index). Reruns of the same config cost nothing and are deterministic. Self-consistency samples must use distinct sample indices so they are not served the same cached answer.
4. A budget guard that aborts the run when cumulative cost passes `budget_usd`.
5. `scripts/make_figures.py` regenerates every paper figure and table from `results/` alone, without calling any model.
6. Write these configs: `calib_main.yaml`, `signals_compare.yaml`, `abstention.yaml`, `write_sweep.yaml`, `baselines.yaml`.

---

## PHASE 9 — UI for the demo

1. **Calibration tab**: reliability diagram (before and after recalibration, switchable), ECE and Brier cards with CIs, risk–coverage curve, and the cost–accuracy frontier. It reads from `results/`; it does not run experiments.
2. **Node inspector**: show every confidence signal side by side, raw against calibrated, calibrator version, valence and intensity.
3. **Chat**: abstentions shown as "asking to confirm", visually distinct from normal replies.
4. **Replay mode**: pick a synthetic persona and step through it turn by turn, so the graph builds, reinforces, supersedes and ends nodes live, with each node's confidence and correctness label shown.

---

## Constraints

- No model-string changes without telling me. If a phase is blocked by a model capability limit, show me the raw outputs and stop.
- Temperature 0 everywhere except self-consistency sampling.
- Never put an eval-set or benchmark message into a prompt as an example.
- All network calls go through `llm.py`. Unit tests never hit the network.
- Keep every existing test passing.

---

# APPENDIX — Model prompts

Generate `{ontology_card}` from `ontology.py`; never hardcode it in a prompt. Every prompt's text is hashed into `prompt_version`.

## P1 — Tier-1 salience (cheap model)

```
You score whether a user's message contains durable information about the user that is worth keeping in long-term memory.

Durable means still useful to know in a week: facts about the user or about people, pets or things in their life; preferences; habits; skills; goals; projects; relationships; changes; corrections; or an answer to a question the assistant just asked.

Not durable: greetings, thanks, questions to the assistant, instructions about formatting or length, temporary states ("tired today"), and one-off actions with no lasting implication ("had tea this morning").

How strongly the user feels does not matter. "i play chess" and "i love playing chess" are equally durable.

Context (oldest first):
{context}

Open question the assistant asked, if any: {pending_slot}

Message: {message}

Return JSON only:
{"salience": <integer 0-100>, "reason": "<at most 12 words>"}
```

The pending-slot salience floor of 60 is applied in code, not by the model.

## P2 — Span extraction (cheap model)

```
Find every clause in the TARGET message that states something durable about the user, or about a person, pet or thing in the user's life. Copy each clause exactly as it appears in the TARGET. Do not rephrase, interpret or classify.

Rules:
- Use the context only to understand short replies. Every span must be copied from the TARGET.
- If the TARGET is a short answer to the assistant's last question (for example "for two years" or "luna"), return the whole TARGET as one span with "answers_slot": true.
- Skip questions, greetings, thanks, temporary moods and one-off actions.
- If nothing qualifies, return an empty list.

Context (oldest first):
{context}

Open question the assistant asked, if any: {pending_slot}

TARGET: {message}

Return JSON only:
{"spans": [{"text": "<exact substring of TARGET>", "answers_slot": true|false}]}
```

## P3 — Structuring (stronger model)

```
You turn one span of a user's message into structured assertions for a knowledge graph of the user's current state.

ONTOLOGY
{ontology_card}

USER'S EXISTING ACTIVE STATE (only the relevant dimensions)
{existing_nodes}
Each line is: id | dimension/key = value | attributes

CONSTRAINTS FROM RULES — these are fixed; do not override them
{constraints}

CONTEXT (oldest first)
{context}

OPEN QUESTION THE ASSISTANT ASKED: {pending_slot}

SPAN: "{span}"

Decide each field in this order.

1. subject — "self" if the span describes the user. If it describes someone or something else, the subject is that entity. In "my cat's name is luna" or "my uncle is a lawyer", the subject is the cat or the uncle, never the user. "my wife is meera" names one of the user's relationships, so its subject is "self" with dimension "relationship".

2. target_node_id — if the span adds detail to a node that already exists above (a duration, a breed, years of experience, a name), give that node's id and put the detail in attributes instead of creating a new node.

3. dimension and key — use only the ontology. The key is snake_case and derived from the content, never "general". Liking and disliking use different keys (for example food_likes and food_dislikes). Before inventing a key, check whether an existing node above already uses a suitable one.

4. value — a short noun phrase of at most 4 words, with no leading verb and no prepositional phrase. "become a doctor", not "is to become a doctor". Move qualifiers into attributes: "masala tea with extra cardamom" becomes value "masala tea" with attribute "spice = extra cardamom".

5. operation —
   ADD     a new assertion (on a single-valued key this replaces the current value)
   UPDATE  restates an existing assertion without changing it
   END     the user stopped, quit, left, broke up, or no longer does or holds this; give the existing node's id in end_node_id
   REFINE  adds detail to an existing node

6. explicitness — "explicit" if stated directly; "hedged" if qualified ("might", "i think", "probably", "planning to"); "inferred" if implied rather than stated.

7. valence and intensity — only for preference, interest and belief assertions; otherwise null. valence is 1 (positive), -1 (negative) or 0 (neutral). intensity is an integer 0-100 judged from the wording only: words like "love" or "hate" around 90, "like" or "dislike" around 60, "kind of", "moderately" or "a bit" around 35.

8. confidence — an integer 0-100 answering one question: how likely is it that this assertion is true of the user right now, given what the user said? This is not how sure you are about the parsing. Hedged and inferred assertions should score lower than explicit ones. Use the whole range; do not default to round numbers like 80 or 90.

If the span supports no assertion, return {"assertions": []}.

Return JSON only:
{"assertions": [{
  "rationale": "<at most 25 words; write this before deciding the other fields>",
  "subject": "self" or {"dimension": "...", "key": "...", "value": "..."},
  "target_node_id": "<id or null>",
  "dimension": "...",
  "key": "...",
  "value": "...",
  "attributes": [{"name": "...", "value": "...", "unit": "<unit or null>"}],
  "operation": "ADD" | "UPDATE" | "END" | "REFINE",
  "end_node_id": "<id or null>",
  "explicitness": "explicit" | "hedged" | "inferred",
  "valence": 1 | 0 | -1 | null,
  "intensity": <integer 0-100 or null>,
  "confidence": <integer 0-100>,
  "quote": "<exact substring of the span>"
}]}
```

## P4 — Chat generation

```
You are a helpful assistant who remembers things about the user.

WHAT YOU KNOW ABOUT THE USER
{state_block}

How to use this:
- Use what you know when it is relevant. Do not talk about having a memory system.
- Only state facts about the user that appear above. If a detail is not listed — including durations, names, dates and amounts — say you don't know, or ask. Never guess or fill in details.
- Items marked [ASK] are uncertain. If your reply depends on one, confirm it with a short question instead of stating it as fact.
- Items marked [ENDED] are no longer true. Mention them only if the user asks about the past.
```

## P5 — QA judge

```
Decide whether a model's answer to a question about a user is correct, compared with the reference answer.

Question: {question}
Reference answer: {gold}
Model answer: {answer}

The answer is "correct" if it gives the same information as the reference. When the reference reflects an update, an answer that gives the older value is "incorrect". An answer that says it does not know, or asks the user to confirm, is "abstained".

Return JSON only:
{"verdict": "correct" | "incorrect" | "abstained", "reason": "<at most 15 words>"}
```

## P6 — Synthetic message verbaliser

```
Write one chat message from a user, in a casual lowercase texting style. It must express exactly the event below and no other facts about the user.

EVENT: {event_json}
STYLE: {style}
- explicit: state it plainly
- hedged: express it with uncertainty ("i might", "i think", "probably")
- indirect: imply it without naming it outright

Recent conversation (oldest first):
{context}

Return only the message text.
```

## P7 — Value equivalence

```
For the user attribute "{dimension}/{key}", do these two values mean the same thing?

A: "{value_a}"
B: "{value_b}"

Treat spelling variants, plurals and synonyms as the same. Treat different specific items as different (for example "green tea" and "black tea").

Return JSON only:
{"same": true | false}
```