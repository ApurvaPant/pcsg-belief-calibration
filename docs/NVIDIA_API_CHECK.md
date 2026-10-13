# NVIDIA build API — health check

**Verdict: PARTIAL** — key + endpoint healthy and the judge works, but the *chosen*
main model **DeepSeek V4.1 Flash times out** (>30 s even for a trivial prompt, in all
reasoning-off variants). A fallback main (`nvidia/nemotron-3-super-120b-a12b`) and the
judge (`openai/gpt-oss-20b`) both respond in ~1.3 s.

_Diagnostic only; no config/pipeline changes. Foreground, 30 s timeout, no retries, 7 live calls._

## Key
- loaded: True · length: 70 · starts with `nvapi-`: True · surrounding whitespace: False

## Model list
- `GET /v1/models` → status **200**
  - ✅ found: `deepseek-ai/deepseek-v4.1-flash`
  - ❌ absent: `nvidia/deepseek-v4.1-flash`
  - ✅ found: `openai/gpt-oss-20b`
  - ❌ absent: `qwen/qwen3-next-80b-a3b-instruct`
  - ✅ found: `nvidia/nemotron-3-super-120b-a12b`

## Per-model chat call (prompt: "Reply with exactly: OK")

| model | status | latency | served | reasoning seen | prompt tok | compl tok | reasoning tok | reply (≤100) |
|---|---|---|---|---|---|---|---|---|
| `deepseek-ai/deepseek-v4.1-flash` | EXC | 30.13s | None | None | None | None | None |  |
| `openai/gpt-oss-20b` | 200 | 1.28s | openai/gpt-oss-20b | True | 70 | 41 | None | OK |
| `nvidia/nemotron-3-super-120b-a12b` | 200 | 1.38s | nvidia/nemotron-3-super-120b-a12b | True | 21 | 43 | 39 | OK |

## DeepSeek reasoning-off (deepseek-ai/deepseek-v4.1-flash)

| param | status | reasoning tokens | reasoning seen | note |
|---|---|---|---|---|
| reasoning_effort=none | EXC | None | None | rejected: ReadTimeout: The read operation timed out |
| reasoning_effort=low | EXC | None | None | rejected: ReadTimeout: The read operation timed out |
| chat_template_kwargs.thinking=false | EXC | None | None | rejected: ReadTimeout: The read operation timed out |

- reasoning-off params ACCEPTED (200): none
- params giving ZERO reasoning tokens: none

## gpt-oss-20b (reasoning_effort=medium)
- status 200, reasoning tokens None, final answer separable from reasoning: True (content vs reasoning_content fields)

## Main-model selection (per the configured fallback order)
1. **DeepSeek V4.1 Flash** — reachable (in `/v1/models`, key valid) but **times out >30 s** on a trivial prompt, including every reasoning-off variant → does not pass the latency gate as probed.
2. `qwen/qwen3-next-80b-a3b-instruct` — **absent** from this key's `/v1/models` → unavailable.
3. **`nvidia/nemotron-3-super-120b-a12b`** — ✅ **200 in 1.38 s**, served id matches, 39 reasoning tokens, reply "OK" → the usable main model on this key as probed.

Judge `openai/gpt-oss-20b`: ✅ 200 in ~1.3 s, final answer separable from reasoning (`content` vs `reasoning_content`).

## Errors & recommendations
- `deepseek-ai/deepseek-v4.1-flash`: **ReadTimeout (no response within 30 s)**. Not 401/403/404 — the model is reachable and the key is valid; it is simply too slow for a 30 s probe. Before relying on it: retest with a higher timeout and/or streaming; find whether a reasoning-off param actually cuts latency (none did within 30 s here). If typical latency stays above the ~8–10 s gate, use the fallback `nvidia/nemotron-3-super-120b-a12b`.
- `nvidia/deepseek-v4.1-flash`: absent from `/v1/models` — this id does not exist on NVIDIA build (use `deepseek-ai/deepseek-v4.1-flash`).
- `qwen/qwen3-next-80b-a3b-instruct`: absent from `/v1/models` — wrong id or not offered on this key; open its model page on build.nvidia.com to confirm the exact id / request access, else skip (fallback order moves to nemotron).
