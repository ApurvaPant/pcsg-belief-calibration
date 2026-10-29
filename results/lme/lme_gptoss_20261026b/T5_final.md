# T5 — LongMemEval (FINAL, recomputed from judged records)

**memory+answering: openai/gpt-oss-20b (reasoning_effort low); judge: minimaxai/minimax-m3.1-flash.**
Nemotron 3 Super was withdrawn from the hosted API 2026-10-25; gpt-oss-20b is retained for the
structured write path because faster models (nemotron-3.5-lightning 0/5, minimax 3/5) did not
return valid 5/5 JSON frames on this endpoint, while gpt-oss-20b did (5/5).
Judge: official per-type prompts verbatim (github.com/xiaowu0162/LongMemEval `evaluate_qa.py`
`get_anscheck_prompt`; MIT).

Recomputed offline from `answers_final.jsonl` (108 unique questions). **This supersedes the
earlier `T5.md`, whose overall column was computed over judged-only records and so did not match
its own per-type numbers.** Here **every system is scored on all 108 questions**, and a
**blank/unparsed judge verdict is counted as wrong** in the main table (an "excluding unjudged"
column is given alongside).

Question mix: knowledge-update (answerable) **n=72**, single-session-preference **n=30**,
abstention/`_abs` **n=6**.

## Accuracy per system — all 108, unjudged = wrong (bootstrap 95% CI, 5000×)

| system | overall [95% CI] | overall (excl. unjudged) | unjudged | KU (n=72) | SSP (n=30) | abstention (n=6) | coverage | selective-acc | tok/q |
|---|---|---|---|---|---|---|---|---|---|
| no_memory    | 0.056 [0.019, 0.102] | 0.056 | 0  | 0.000 | 0.000 | 1.000 | 0.000 | —     | 192.8 |
| full_context | 0.593 [0.500, 0.685] | 0.744 | 22 | 0.792 | 0.033 | 1.000 | 0.863 | 0.879 | 5735.2 |
| vector_rag   | 0.546 [0.454, 0.639] | 0.663 | 19 | 0.736 | 0.033 | 0.833 | 0.814 | 0.844 | 528.3 |
| pcsg         | 0.120 [0.065, 0.185] | 0.149 | 21 | 0.083 | 0.033 | 1.000 | 0.353 | 0.467 | 521.5 |

- *overall* = correct / 108 with unjudged scored wrong; *overall (excl. unjudged)* = correct / (judged).
- *KU/SSP* columns also score unjudged as wrong over their full n (72 / 30); weighting them by n
  reproduces *overall* exactly (e.g. full_context (72·0.792 + 30·0.033 + 6·1.000)/108 = 0.593).
- *coverage* = fraction of the 102 answerable questions the system answered (did not abstain);
  *selective-acc* = accuracy on answered-and-judged questions.

## Blank/unparsed verdicts (counted wrong above) — by type

| system | total unjudged | single-session-preference | knowledge-update |
|---|---|---|---|
| no_memory    | 0  | 0  | 0 |
| full_context | 22 | 22 | 0 |
| vector_rag   | 19 | 17 | 2 |
| pcsg         | 21 | 18 | 3 |

The unjudged records are **overwhelmingly single-session-preference** and share one root cause
with the near-zero SSP scores (next section): the SSP answers overrun the 256-token generation
cap, so their JSON never closes and the judge receives a truncated `{"answer": "…` blob and emits
no parsable yes/no. no_memory is unaffected because it abstains with a short clean reply.

## Why single-session-preference ≈ 0 for every memory system (truncation, not memory)

SSP answers are long personalized recommendations. With `ANSWER_MAX_TOKENS = 256`, the model is
cut off mid-string, so `baselines._parse`'s `{.*}` regex finds no closing brace, `json.loads`
fails, and the raw `{"answer":"…` wrapper is kept verbatim as the answer. The judge then scores a
truncated, JSON-prefixed fragment.

- SSP completions hitting the cap (≥250 tok): full_context **19/30**, vector_rag **14/30**, pcsg **15/30**.
- SSP answers left as unclosed JSON (wrapper leaked): **46/120** across the three memory systems.
- The fix-up's unwrap step recovered **0** of these — incomplete JSON has no closing brace to parse.
- Net SSP accuracy: full_context **1/30 (0.033)**, vector_rag 0.033, pcsg 0.033, no_memory 0.000.

3 examples (all judged blank → counted wrong):

1. `8a2466db` / full_context — Q "recommend resources to learn video editing" · ctok=**256**, truncated, wrapped · answer begins `{"answer":"Here are a few resources … **1. Adobe Creative Cloud Tutorials** …` (cut off, no closing brace) · judge_raw = *(blank)*.
2. `8a2466db` / vector_rag — same question · ctok=**256**, truncated, wrapped · `{"answer":"Here are some of the most popular … resources (as of 2023-05-24) …` (cut off) · judge_raw = *(blank)*.
3. `195a1a1b` / vector_rag — Q "suggest evening activities" · ctok=**256**, truncated, wrapped · `{"answer":"Here are a few evening activities … 1. Light stretching or yoga …` (cut off) · judge_raw = *(blank)*.

**Fix (not applied — would need a re-run):** raise/stream the answer cap for SSP or stop-sequence on the
closing brace, and make `_parse` salvage an unterminated `{"answer":"…` by extracting the string value.

## Fabrication on the 6 abstention (`_abs`) questions

| system | fabrication-rate (answered an unanswerable) |
|---|---|
| no_memory | 0.000 |
| full_context | 0.000 |
| vector_rag | 0.167 |
| pcsg | 0.000 |

## McNemar (exact) on answerable questions, both verdicts parsed

- PCSG vs vector_rag: pcsg-only-right=**2**, vector_rag-only-right=**47**, exact p=4.4e-12 (n=76).
- PCSG vs full_context: pcsg-only-right=**1**, full_context-only-right=**50**, exact p=4.6e-14 (n=74).

PCSG is decisively behind both memory baselines on answerable questions; see `PCSG_FAILURES.md`
(recall/aggregation-bound, not retrieval-bound).

## Fix-up pass accounting (2026-10-26, after main run exit)

- Questions re-run after HTTP errors: **13 attempted, 13 now OK, 0 still failed** → all 108 present.
- Answers unwrapped from a *parseable* `{"answer": …}`: **0** (the leaked wrappers are truncated/unclosed, so unwrap could not apply — see SSP section).
- Blank/unparsed verdicts re-judged by the fix-up: **63**; **remaining unjudged after re-judge:
  full_context 22, vector_rag 19, pcsg 21, no_memory 0** (SSP-dominant, per the table above).
