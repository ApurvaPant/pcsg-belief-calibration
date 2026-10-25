# Overnight run log

## 2026-10-25 — model availability incident (RECORDED FOR THE PAPER)

**FACT:** `nvidia/nemotron-3-super-120b-a12b` — the main study's model
(run `main_20261015_000553`) — was **withdrawn from the NVIDIA hosted API**.
Confirmed removed from the build.nvidia.com catalog by the user at 2026-10-25
19:49 IST (only Nemotron 3 Ultra / Nano and the 3.5 family remain listed).

Live checks from this session (provider=nvidia, key present):
- 2026-10-25 ~18:55 IST — `POST /v1/chat/completions` model
  `nvidia/nemotron-3-super-120b-a12b` → **HTTP 404** `{"status":404,
  "title":"Not Found","detail":"Function 'ac74040f-9fc9-4c5e-ac74-279ba5161d69':
  Not found for account '...'"}` (account-level entitlement lost; also with the
  main-run request param `chat_template_kwargs.enable_thinking=false`).
- Models checked and WORKING on this key: `openai/gpt-oss-20b` (OK),
  `nvidia/nemotron-3.5-super-vl-120b-a12b` (OK, served
  `private/nvidia/nemotron-3.5-super-120b-a12b`), `nvidia/nemotron-3.5-lightning-30b-a3b`
  (OK), `minimaxai/minimax-m3.1-flash` (OK).
- Not usable: `moonshotai/kimi-k2.6` (404), `z-ai/glm-5.3` /
  `deepseek-ai/deepseek-v4.1-flash` (read-timeout, unconfirmed — not used).

**Decision (user, final):** the main run stays READ-ONLY and is the main study.
All NEW runs tonight use `openai/gpt-oss-20b` (reasoning_effort=low on write-path
calls; reasoning text stripped before parsing) for memory/extraction/answering,
and `minimaxai/minimax-m3.1-flash` as the LongMemEval judge and the paraphraser.
No Nemotron 3.5 model is used. Every new result is labelled
"model: gpt-oss-20b (Nemotron 3 Super withdrawn from the hosted API on 2026-10-25)".

---
