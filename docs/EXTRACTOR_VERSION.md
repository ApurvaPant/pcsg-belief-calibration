# EXTRACTOR_VERSION

Authoritative record of the model string bound to every pipeline/eval role, the
prompt version, and the key pipeline thresholds. The readiness audit (PHASE 1.1)
diffs the live config against this file; any mismatch is a blocker.

- **This file did not exist before 2026-10-13.** It was created during the
  offline readiness check by snapshotting the current committed config
  (`backend/config.yaml`) and `extraction.PROMPT_VERSION`. It therefore matches
  the config at creation by construction; its purpose is to catch *future* drift.

## prompt_version
`2.0-frame-fill`  (`backend/app/extraction.py:PROMPT_VERSION`)

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
