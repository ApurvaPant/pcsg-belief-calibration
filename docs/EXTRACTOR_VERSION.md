# EXTRACTOR_VERSION

Authoritative record of the model string bound to every pipeline/eval role, the
prompt version, and the key pipeline thresholds. The readiness audit diffs the
live config against this file; any mismatch is a blocker.

---

## v2 — NVIDIA build (2026-10-14, tag `extractor-v2-nvidia`)

**Provider:** `nvidia` (`https://integrate.api.nvidia.com/v1`, OpenAI-compatible).
**prompt_version:** `2.0-frame-fill` — UNCHANGED (no prompt content edited; provider/model swap only).

### Models (all pipeline roles = the one main model)
| role | model | params |
|---|---|---|
| chat / classifier / extractor / simulator (gate, span, structuring, self-consistency, P6, answerer ×4 systems, P7) | `nvidia/nemotron-3-super-120b-a12b` | `chat_template_kwargs: {enable_thinking: false}` |
| judge + judge_longmemeval | `openai/gpt-oss-20b` | `reasoning_effort: "medium"` (final answer only; reasoning stripped) |
| embedding (vector_rag, local, not an API call) | `sentence-transformers/all-MiniLM-L6-v2` | — |

- **Thinking-off setting (validated):** `chat_template_kwargs: {"enable_thinking": false}` on Nemotron.
  Over 10 extraction prompts: **median reasoning tokens 0**, **median latency 0.9 s**, **0/10 with `<think>` text**, clean JSON. (Also fully disabled by `chat_template_kwargs:{thinking:false}` and `reasoning_effort:"none"`; `/no_think` and `reasoning_effort:"low"` did NOT disable it.) This exact param is applied to EVERY pipeline call.
- DeepSeek V4.1 Flash was rejected (timed out >30 s, `docs/NVIDIA_API_CHECK.md`); Qwen is not on this key. No automatic fallback beyond Nemotron.

### Temperatures / thresholds (unchanged)
- temperature: 0 for extraction + structuring; 0.7 for self-consistency (k=3). max_tokens 1024, timeout 60 s.
- salience_threshold 0.5 (replay opens to 0.0); calibration_prior 0.5, shrink 0.8; reinforce_step 0.1; resolve_similarity 0.92; destructive_min_conf 0.80.

### Eval scores — Nemotron, live, `extractor_eval_cases.json` (7 cases/split, runs=1)
| split | pass | P | R | F1 | END prec | destructive err | false-write |
|---|---|---|---|---|---|---|---|
| dev  | 3/7 = 42.9% | 50% | 50% | 50% | 100% | 0% (0/4) | 50% |
| test | 3/7 = 42.9% | 50% | 50% | 50% | 100% | 0% (0/4) | 50% |

Field-level (test): dimension 80%, key 60%, operation 100%, value 100%. Weak categories
both splits: refinement-chain/attribute, negated-preference/property; strong: negated-relation,
END precision. Format-compliance is clean (no JSON parse failures; misses are content/key).

**No prior real-model baseline exists** (the earlier provider never ran these cases live; previous
PROGRESS numbers were the deterministic mock at ~100% on these authored chains). So the
">15-point drop vs previous model" stop-rule has no real baseline to compare against, and a
real-vs-mock gap is not a regression. The 7-case set is a tiny, mock-authored probe (the
45-case human benchmark was never supplied); the experiment's headline results are the
*calibration* of confidence vs correctness, for which extraction accuracy is a measured input,
not a pass/fail gate. No prompt content was tuned (only provider/model config changed).

---

## v1 — snapshot (2026-10-13)

- **This file did not exist before 2026-10-13.** It was created during the
  offline readiness check by snapshotting the then-current config
  (`backend/config.yaml`, gemini) and `extraction.PROMPT_VERSION`. Superseded by v2 above.

## prompt_version
`2.0-frame-fill`  (`backend/app/extraction.py:PROMPT_VERSION`) — unchanged.

## Bug fixes (behaviour-affecting, no prompt change)
- **2026-10-13 — self_consistency null on entity-subject assertions.** The
  entity-subject path in `update.apply_assertion` (relationship facts with
  attributes, e.g. "my cat's name is mochi", "my sister is a doctor") rebuilt a
  minimal assertion dict for the entity node and dropped the attached
  `self_consistency` (and `explicitness`/`intensity`), so those rows stored a
  silent null. Fix: propagate the signal-bearing fields onto the entity
  assertion; `signals.write_signals` now also records a `self_consistency_reason`
  (`sc_disabled` / `sc_unattached`) so a null is never silent. Coverage of all
  three signals is 100% on an oracle_mock replay with resampling enabled
  (`tests/test_self_consistency_coverage.py`). **prompt_version unchanged** (no
  prompt was modified).

## Role → model (provider: `gemini`, verified in code)

| Paper role | Pipeline stage (code) | config role | model string |
|---|---|---|---|
| tier-1 gate | `salience.py` classify | `classifier` | `gemini-2.5-flash-lite` |
| span extraction | FRAME planner (`extraction.py:430`) | `classifier` | `gemini-2.5-flash-lite` |
| structuring | FILL (`extraction.py:412`, `pipeline.py:497`) | `extractor` | `gemini-2.5-flash` |
| self-consistency | k=3 FILL resamples (`signals.py`) | `extractor` | `gemini-2.5-flash` |
| verbaliser P6 | extractor's own confidence (`signals.py`) | `extractor` | `gemini-2.5-flash` |
| value-equivalence P7 | labelling synonym stub offline; judge live (`analysis/labelling.py`) | `judge` (live) | `gemini-2.5-pro` |
| answerer | chat reply (`pipeline.py:468`) | `chat` | `gemini-2.5-flash` |
| judge (default) | evaluation (`robustness/runner.py:90`) | `judge` | `gemini-2.5-pro` |
| judge (LongMemEval) | evaluation, official per-type prompts | `judge_longmemeval` | `openai/gpt-4o` |

## Thresholds (`config.yaml:pipeline`)
- `salience_threshold`: 0.5  (replay opens the gate to 0.0)
- `calibration_prior`: 0.5, `calibration_shrink`: 0.8
- `reinforce_step`: 0.1, `resolve_similarity`: 0.92, `destructive_min_conf`: 0.80
- `max_tokens`: 1024, `temperature`: 0.2, `timeout_seconds`: 60

## Known provider mismatch (BLOCKER for live)
The paper's live run targets **OpenRouter** with `openai/gpt-4o` / `openai/gpt-4o-mini`,
but every pipeline role above is bound to **gemini** slugs and `llm.base_url`
points at the Gemini endpoint. Changing pipeline model strings is out of scope
for this readiness session (forbidden); reconciling the provider is a human
decision before live. Only `judge_longmemeval` (eval-only) was added this session.
