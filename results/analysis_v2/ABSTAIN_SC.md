# ABSTAIN_SC — self-consistency-based abstention (analysis_v2 item 4)

**STATUS: SKIPPED — the required per-question provenance does not exist in the main run.**

## What this analysis needs
Self-consistency-based answer confidence requires, for each of the 264 Tier-2 QA
questions, **which stored assertions PCSG used to produce that answer** (retrieval
IDs / supporting-assertion node ids per question). The answer confidence would then be
the Platt-calibrated self-consistency (fit on the fit personas) of those supporting
assertions (max over used assertions; none → abstain), and the risk–coverage / AURC
curve would be compared to the verbalized-answer curve on the same questions.

## What was checked (read-only, offline)
- `results/runs/main_20261015_000553/qa_systems.json` — **aggregates only**: per-system
  `accuracy`, `coverage`, `selective_accuracy`, `fabrication_rate`, `aurc`, `tokens_per_q`,
  `answer_ece`, and a `sweep` (tau grid). No per-question records, no retrieved/used
  assertion ids, no per-question answer text.
- `results/runs/main_20261015_000553/` scanned for any file containing retrieval /
  used-assertion / supporting / evidence-id / node-id provenance — **none found**.
- `baselines.json`, `abstention.json` — aggregate curves only.
- The run's SQLite DB (which could, in principle, hold a per-question trace) has been
  removed from TEMP and is unavailable; even when present it did not record
  per-question → used-assertion links.

## Decision
Per the task instruction ("if they do NOT exist … state exactly what is missing, and
skip; do not approximate silently"), this analysis is **not performed**. Producing a
self-consistency abstention curve would require re-running Tier-2 QA with per-question
assertion-usage logging — a **live** experiment (and Nemotron 3 Super, the main model,
has since been withdrawn from the hosted API; see docs/REPRODUCE.md "Known gaps").

**Recommendation for a future live run:** persist, per QA question, the ids of the PCSG
assertions injected into the answer context (and the chosen answer + verdict). Then this
curve is reproducible offline.
