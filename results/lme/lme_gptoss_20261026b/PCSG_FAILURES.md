# PCSG failure analysis — LongMemEval (lme_gptoss_20261026b)

memory+answering `openai/gpt-oss-20b` (reasoning_effort low); judge `minimaxai/minimax-m3.1-flash`.
Reconstructed **offline** from `answers_final.jsonl` + `llm_cache.jsonl` (no API calls).

## Method

For every **answerable** PCSG question that was not correct (`correct != 1`), I recovered the
PCSG answer prompt's context from the `[[TASK:QA_ANSWER]]` cache entry. PCSG retrieval is
**non-selective**: `answer_pcsg` injects the *entire active profile* (`_profile_summary`,
`status='active'` only — superseded nodes excluded), prefixed `Known about the user so far:`.
So for the active set, **"retrieved" ≡ "the gold fact is present in that profile block."**
A belief that was extracted but later superseded, or never extracted, is simply absent.

- **(a) extracted & (b) retrieved** collapse into one observable: gold present in the profile block.
- gold match = normalized substring / number-word equivalence (four↔4) / ≥60 % content-word overlap,
  hand-verified on the examples below.
- Count/aggregate gold = question asks "how many / how much / how often …" or gold is a bare number.
- 6 `_abs` (unanswerable) questions are **excluded** — PCSG abstains on all 6, which is correct.

## Counts (95 answerable PCSG failures of 102; PCSG got only ~7 answerable right)

| category | n | of which count/aggregate |
|---|---|---|
| **A. Retrieved but mis-answered** — gold was in the profile, yet PCSG abstained or gave a wrong value | 12 | 6 |
| **B. Profile present but gold absent** — a non-empty profile, but the gold fact never became an active node (extraction / reconciliation miss, or superseded) | 61 | 29 |
| **C. Empty profile** — nothing durable was stored for the user | 1 | 0 |
| **Unjudged** — judge returned a blank/unparsed verdict (counted wrong in T5); 18 of 21 are single-session-preference truncation casualties, see T5_final §SSP | 21 | 0 |
| **total** | **95** | **35** |

**Headline:** the dominant failure (B, 61/95) is that the gold fact is *not in the active
profile at answer time*; **35/95 (37 %) ask for a count/aggregate** ("how many X", "how often"),
which PCSG structurally cannot answer — it stores discrete typed assertions, not running tallies,
so no single node holds "4 Korean restaurants" and it abstains. Only 12 failures reach the
answerer with the fact present (A), and even then it often returns a stale/superseded value.

## 5 examples

1. **B / count** `6aeb4375` — Q "How many Korean restaurants have I tried in my city?" · gold **four** · PCSG **"I don't know."** Visits are separate episodic facts; PCSG never aggregates them into a count.
2. **B / count** `72e3ee87` — Q "How many episodes of the Science series have I completed on Crash Course?" · gold **50** · PCSG **"I don't know."** Same: no tally node.
3. **B / preference** `38146c39` (SSP) — Q "…my chocolate chip cookies need something extra. Any advice?" · gold = *prefer responses building on their past experiments* · PCSG **"I don't know."** The standing preference was never captured as a `preference` node.
4. **A / wrong value** `7a87bd0c` — Q "How long have I been sticking to my daily tidying routine?" · gold **4 weeks** · PCSG **"…for 3 weeks."** The fact was retrieved but the value is stale — a superseded/duration-update that reconciliation did not advance.
5. **A / count wrong** `89941a93` — Q "How many bikes do I currently own?" · gold **4** · PCSG **"1."** The node existed but held a single early value; later acquisitions were not merged into the count.

## Takeaways

- PCSG's low answerable accuracy is **recall/representation-bound, not retrieval-bound**: once a
  fact is an active node it is always passed to the answerer (bucket C is a single question).
- Two structural gaps dominate: **(i) no aggregation** — count/aggregate questions (37 % of
  failures) have no home in a discrete-node store; **(ii) reconciliation lag** — bucket A shows
  the latest value/count is sometimes not promoted, so a stale node is retrieved.
- SSP failures are partly a *harness* artifact (256-token truncation → blank verdicts), not a
  memory defect — quantified in `T5_final.md`.
