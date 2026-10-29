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
- 2026-10-25 20:54:46 === OVERNIGHT START run=20261025_205446 hardstop=01:30 models=gpt-oss-20b+minimax (Nemotron 3 Super withdrawn) ===
- 2026-10-25 20:54:46 [STEP4] start: pcsg.run model2_sc (only-personas 27,31,33,34,36,37,38,39,40,41,45,47, replay-workers 12)
- 2026-10-26 00:28:43 [STEP4] done (MODEL2.md written)
- 2026-10-26 00:28:43 [STEP1] pilot (5 questions) to size the full set
- 2026-10-26 00:33:24 [STEP4] complete (12/12, MODEL2.md written). [STEP2/3 deferred; LongMemEval run started instead] — overnight orchestrator (PID 11452) + its python terminated before STEP 2; key free.
- 2026-10-26 00:37:37 [LME] start run=lme_gptoss_20261026 models=gpt-oss-20b(ans)+minimax(judge) order=abs-first,2:1 cutoffs 08:15/08:30
- 2026-10-26 01:15:49 [LME] pilot(4) retry_rate=0
- 2026-10-26 01:15:49 [LME] full run workers=4 until=08:15 hard=08:30
- 2026-10-26 01:20:34 [LME] run ended (see C:\Users\Dell\OneDrive\Desktop\Final Year Project\Chat Interface\results\lme\lme_gptoss_20261026\T5.md, run_metrics.json)
- 2026-10-26 01:40:57 [LME] start run=lme_gptoss_20261026b memory=gpt-oss-20b judge=minimax 4w/90s/3retry order=abs-first,2:1 cutoffs 08:15/08:30
- 2026-10-26 01:40:57 [LME] 2-question end-to-end test (first 2 of ordered list)
- 2026-10-26 01:53:37 [LME] TEST PASSED: 2 questions completed. Launching full ordered run.
- 2026-10-26 03:36:42 [LME-FIXUP] fix-up launcher armed; waiting for lme_overnight to exit (poll 5 min).
- 2026-10-26 05:34:08 [LME] full run ended (done=108); see C:\Users\Dell\OneDrive\Desktop\Final Year Project\Chat Interface\results\lme\lme_gptoss_20261026b\T5.md
- 2026-10-26 05:36:47 [LME-FIXUP] lme_overnight process has exited; starting fix-up.
- 2026-10-26 05:36:48 [LME-FIXUP] loaded 108 unique records; 13 failed to re-run; stop-new at 09:10
- 2026-10-26 05:42:40 [LME-FIXUP] re-ran 38146c39 OK
- 2026-10-26 05:44:07 [LME-FIXUP] re-ran 7e974930 OK
- 2026-10-26 05:49:49 [LME-FIXUP] re-ran 3ba21379 OK
- 2026-10-26 05:52:03 [LME-FIXUP] re-ran 1a1907b4 OK
- 2026-10-26 05:53:33 [LME-FIXUP] re-ran 6071bd76 OK
- 2026-10-26 05:59:49 [LME-FIXUP] re-ran 0a34ad58 OK
- 2026-10-26 06:01:34 [LME-FIXUP] re-ran 0ddfec37 OK
- 2026-10-26 06:02:14 [LME-FIXUP] re-ran 26bdc477 OK
- 2026-10-26 06:06:43 [LME-FIXUP] re-ran 0edc2aef OK
- 2026-10-26 06:09:00 [LME-FIXUP] re-ran 59524333 OK
- 2026-10-26 06:10:03 [LME-FIXUP] re-ran 1da05512 OK
- 2026-10-26 06:13:50 [LME-FIXUP] re-ran a89d7624 OK
- 2026-10-26 06:17:17 [LME-FIXUP] re-ran 22d2cb42 OK
- 2026-10-26 06:53:36 [LME-FIXUP] re-ran-ok=13 still-failed=0 unwrapped=0 re-judged=63
- 2026-10-26 06:53:36 [LME-FIXUP] wrote T5_final.md (108 judged); judge_check_lme.csv regenerated. DONE.
- 2026-10-26 06:53:37 [LME-FIXUP] fix-up launcher finished (python exit 0).
