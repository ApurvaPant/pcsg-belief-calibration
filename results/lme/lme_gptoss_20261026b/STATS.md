# LongMemEval selection stats

**memory+answering: openai/gpt-oss-20b (reasoning_effort low); judge: minimaxai/minimax-m3.1-flash. Nemotron 3 Super was withdrawn from the hosted API 2026-10-25; gpt-oss-20b is retained for the structured write path because faster models (nemotron-3.5-lightning 0/5, minimax 3/5) did not return valid 5/5 JSON frames on this endpoint, while gpt-oss-20b did (5/5).**

| type | n | sessions (mean/min/max) | user-turns (mean/min/max) | user-tokens (mean/min/max) |
|---|---|---|---|---|
| abstention | 6 | 2.0/2/2 | 11.8/11/12 | 509.3/416/588 |
| knowledge-update | 72 | 2.0/2/2 | 11.7/10/12 | 512.8/320/828 |
| single-session-preference | 30 | 1.0/1/1 | 7.2/6/10 | 215.3/121/323 |