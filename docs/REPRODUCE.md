# Reproducing the results

Everything in the paper's analysis is regenerated **from the committed run folders with
zero API calls**. Only re-running the experiments themselves needs a model API.

## Fresh clone
```bash
git clone <repo> && cd "Chat Interface"
python -m venv backend/.venv
backend/.venv/Scripts/pip install -r backend/requirements.txt   # Windows
# backend/.venv/bin/pip install -r backend/requirements.txt     # Linux/macOS
```

## Reproduce all tables + figures (NO API)
```powershell
powershell -ExecutionPolicy Bypass -File scripts\reproduce.ps1   # Windows
```
```bash
bash scripts/reproduce.sh                                        # Linux/macOS/Git Bash
```
This sets `PCSG_OFFLINE=1` (any non-mock LLM call raises), then:
1. `make_figures.py` → `results/tables/` (T1–T4) + `results/figures/` (F1–F3) + `results/dashboard.json`;
2. `scripts/analysis_v2/{sig_recalib,templates,evidence_v2,human_audit}.py` →
   `results/analysis_v2/{STATS,TEMPLATES,EVIDENCE_V2,HUMAN_AUDIT}.md` (+ `ABSTAIN_SC.md`, static);
3. `make_manifest.py --check` → SHA-256 of every input + output vs `results/MANIFEST.json`
   (hard-fails on input/CSV/MD/JSON drift; PNG diffs warn only, as they are matplotlib-backend dependent).

**Expected runtime:** ~2–4 minutes on a laptop (bootstraps are seeded: `seed=2026`). Deterministic:
a second run prints `CHECK OK: 15 inputs + 23 outputs match`.

## What needs the API (re-running experiments) vs what does not
| task | API? |
|---|---|
| All tables/figures, calibration, significance, cost sweep, label sensitivity, human-audit metrics | **No** — `reproduce.ps1/.sh` |
| Re-executing Tier-1 replay / Tier-2 QA / LongMemEval / paraphrase (generating new logs) | **Yes** — `pcsg.run` / overnight runners; an OpenAI-compatible key in `backend/.env` |

## Known gaps
- **Per-item Tier-2 QA answers and verdicts were not persisted** in the main run — only
  per-system aggregates (`qa_systems.json`). Consequently: no exact McNemar / per-question
  bootstrap on QA (STATS.md §2d), no self-consistency abstention curve (ABSTAIN_SC.md), and
  no `judge_check_main.csv`. A future live run should log, per question, the PCSG assertion
  ids used + the chosen answer + verdict.
- **Nemotron 3 Super was withdrawn from the NVIDIA hosted API on 2026-10-25** (account-level
  404; removed from build.nvidia.com). The main run (`main_20261015_000553`) is therefore
  **reproducible from its logs and labelled data but cannot be re-executed live** with the
  same model. New experiments after that date substitute `openai/gpt-oss-20b` (+ `minimax`
  judge) and are labelled as such (see `results/OVERNIGHT_LOG.md`).
- **`cost.json` token total** for the main run was overwritten by a later `--resume` before
  the clobber fix; the headline "22.5M tokens" is not recoverable from a committed artifact
  (the 12,787-call count survives in `results/QUEUE_STATUS.md`). See `docs/PAPER_NUMBERS.md`.
- **Signal↔evidence-text linkage** (TEMPLATES/EVIDENCE_V2/HUMAN_AUDIT): the run's SQLite DB
  that joined each confidence signal to its message text was cleaned from TEMP, so text-linked
  analyses recover the link by (persona,dimension[,value]) alignment and report coverage
  (≈68% of test beliefs; 61/78 audited). Stated in each file; not approximated silently.
- **Deleted run database.** The main run used a SINGLE shared experiment DB,
  `%TEMP%/pcsg_exp_main_20261015_000553.db` (all 60 personas in one DB, keyed by user_id —
  it was never per-persona), and it has been removed from TEMP. The LongMemEval stage also
  creates ephemeral per-QUESTION DBs (`%TEMP%/lme_*.db`) that are unlinked by design, so none
  persist. Losing the experiment DB prevents, from committed artifacts alone: (a) exact
  per-belief signal→message-text linkage (`confidence_signals.message_id → messages.content →
  nodes.user_id`); (b) recovery of the per-message write-path token columns
  (`gate_log.frame_tokens/fill_tokens`, `extraction_log`) — only the aggregate extraction
  total **5,634,624** survives (in `cost_curve.json` at tier1=0); (c) the full token meter /
  per-persona token accounting (the 22.5M figure — see PAPER_NUMBERS.md); (d) exact per-belief
  explicitness and evidence spans (hence the post-hoc hedge lexicon on the ~68% subset). Call
  counts survive in `results/QUEUE_STATUS.md` / `queue_state.json` (tier1 12,787; tier2 QA 1,883).
