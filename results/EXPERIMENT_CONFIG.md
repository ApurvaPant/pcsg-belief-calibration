# PCSG v2 - Experiment Configuration (frozen at pcsg-v2-exp)

- git sha: `720a27c64e15e934da6f519da9de1fc84b5a9f84`
- provider: `openrouter`  base_url: `https://openrouter.ai/api/v1`
- temperature (default): 0.2   max_tokens (COMMITTED): 1024
- timeout_seconds: 60

## Model roles
- chat: `openai/gpt-4o-mini-2024-07-18`
- classifier: `meta-llama/llama-3.1-8b-instruct`
- extractor: `openai/gpt-4o-mini-2024-07-18`
- simulator: `meta-llama/llama-3.1-8b-instruct`
- embedding: `openai/text-embedding-3-small`

## Pipeline thresholds
- salience_threshold: 0.5
- calibration_prior: 0.5
- calibration_shrink: 0.8
- reinforce_step: 0.1
- resolve_similarity: 0.92
- destructive_min_conf: 0.8
- FILL self-consistency: k=3 @ temp 0.7; verbalized=extractor conf; evidence=NoisyOR(verbalized, explicitness prior 0.70 explicit / 0.40 implicit)

## Prompt hashes (sha256[:16])
- FRAME:  `39339f174c1d5248`
- FILL:   `93a5fa7da282f88b`
- CHAT:   `8cb8a28ee9bec370`
- SALIENCE classifier: `1e3bfe3499368bfc`
- QA_RESOLVE: `c145f3e20648d777`
- PROMPT_VERSION: 2.0-frame-fill

## Integrity
No pipeline or prompt changes after this tag - only experiment code. Experiment
runs use the committed max_tokens (1024), never the test-only 512 cap in runners.
