# Are LLM Agents' Beliefs About Users Calibrated? Confidence and Cost in Evolving User-State Memory

**Apurva Pant · Jithumol Thankachan · Raj Thakur · Ujjwal Kumar Yadav**
KPR Institute of Engineering and Technology

Code and reproducibility package for the paper. This repository contains the
**PCSG** system, the experiment harness, and every input and output needed to
regenerate the paper's tables and figures **offline, with zero API calls**.

## What is PCSG?

The **Personal Cognitive State Graph (PCSG)** is a temporal, evidence-backed
memory of a user that an LLM agent builds as it talks to them. Each turn is
gated for salience and, when it carries durable information, distilled into
typed assertions about the user — preferences, goals, skills, projects, habits,
beliefs, relationships, communication style. Every assertion is a node carrying
a raw and a *calibrated* confidence, the exact utterances that evidence it, and
a validity interval. When the user later contradicts an earlier assertion the
old node is **not overwritten**: it is closed off and superseded, and both
versions remain in the graph. The paper studies whether the agent's *confidence*
in these beliefs is **calibrated**, and the accuracy/token **cost** trade-off of
writing and reading this memory versus full-context and vector-RAG baselines.

## Repository contents

| Path | What it is |
|---|---|
| `backend/` | FastAPI turn pipeline, SQLite store, provider-agnostic LLM client (`app/llm.py`), extraction/reconciliation/calibration, PCSG experiment code (`pcsg/`) |
| `frontend/` | React + Vite + TypeScript chat UI with the Cytoscape.js graph |
| `results/runs/main_20261015_000553/` | The main run's artifacts (ground-truth inputs for the paper numbers) |
| `results/tables/`, `results/figures/` | Paper tables (T1–T4) and figures (F1–F3) |
| `results/analysis_v2/` | Robustness & significance analysis (STATS, TEMPLATES, EVIDENCE_V2, HUMAN_AUDIT, ABSTAIN_SC) |
| `results/lme/lme_gptoss_20261026b/` | LongMemEval run (T5): answers, judged records, `T5_final.md`, `PCSG_FAILURES.md`, human judge check (`JUDGE_CHECK.md`, `judge_check_lme_human.csv`) |
| `results/model2/` | Second-model comparison |
| `results/MANIFEST.json` | SHA-256 of every reproducibility input and output |
| `docs/REPRODUCE.md`, `docs/PAPER_NUMBERS.md` | How to reproduce; provenance of every headline number |
| `docs/dev-notes/` | Internal design and development notes |
| `scripts/reproduce.ps1`, `scripts/reproduce.sh` | One-command offline reproduction + manifest check |

## Reproducing the paper (offline, no API key)

Every table, figure and analysis file regenerates from the committed run folders
with **zero model calls** (the scripts set `PCSG_OFFLINE=1`, so any non-mock LLM
call raises), then a SHA-256 manifest check confirms the outputs are bit-for-bit
the committed artifacts.

Supported Python: **3.11–3.14**. Install the app and analysis dependencies (ranges,
so wheels resolve on 3.14 without a C compiler):

```bash
python -m venv backend/.venv
backend/.venv/Scripts/python -m pip install -r backend/requirements.txt -r requirements-analysis.txt   # Windows
# backend/.venv/bin/python -m pip install -r backend/requirements.txt -r requirements-analysis.txt      # Linux/macOS
```

```powershell
# Windows PowerShell
powershell -ExecutionPolicy Bypass -File scripts\reproduce.ps1
```
```bash
# Linux / macOS
bash scripts/reproduce.sh
```

Expected tail: `REPRODUCE: OK (zero API calls)`. See **`docs/REPRODUCE.md`** for
details and **`docs/PAPER_NUMBERS.md`** for the source of each headline number.

## Running the interactive app

Two processes:

| Part      | Stack                              | Port | Purpose                                  |
|-----------|------------------------------------|------|------------------------------------------|
| Frontend  | React + Vite + TypeScript          | 5173 | Chat UI + Cytoscape.js graph             |
| Backend   | Python + FastAPI + SQLite          | 8000 | Turn pipeline + temporal graph store     |

CORS is enabled from the backend for `http://localhost:5173`.

### The turn pipeline (backend)

```
message ─▶ salience gate ─▶ [salient?] ─▶ extraction ─▶ reconciliation ─▶ SQLite
                                │                            │
                                └─ most turns dropped        ├─ new value      → CREATE node
                                                             ├─ same value     → REINFORCE (bump confidence)
                                                             └─ contradiction  → SUPERSEDE old + link new
```

Each node carries: `value`, `confidence_raw`, `confidence_calibrated`,
`valid_from`, `valid_to`, `status` (active | superseded | deleted), `evidence`
(pointers to the exact utterances) and `previous_node_id` (the value it
replaced). Supersession is a temporal close, never a destructive update.

### Provider-agnostic LLM (`backend/app/llm.py`)

The **only** module that touches the network. It speaks the OpenAI-compatible
HTTP API via `httpx` (no vendor SDK), reading `OPENAI_BASE_URL`,
`OPENAI_API_KEY`, and model names from `config.yaml` / `.env`. Swap `base_url`
alone to target OpenAI, Anthropic (compatible endpoint), Groq, OpenRouter, or a
local vLLM / Ollama server.

It exposes:

```python
complete(messages, model, temperature, max_tokens) -> {text, prompt_tokens, completion_tokens, latency_ms}
embed(texts, model) -> list[list[float]]
```

Set `LLM_PROVIDER=mock` (the default when no API key is present) for a
**deterministic, offline** provider — the whole pipeline and test-suite run
with no key and no cost.

### Backend

```bash
cd backend
py -3.13 -m venv .venv                 # Python 3.11–3.14 supported
./.venv/Scripts/python -m pip install -r requirements.txt
cp .env.example .env                   # optional; defaults to mock provider
./.venv/Scripts/python -m uvicorn app.main:app --reload --port 8000
```

### Frontend

```bash
cd frontend
npm install
npm run dev                            # http://localhost:5173
```

Open http://localhost:5173. With no API key set it runs on the mock provider,
so you can demo the full flow immediately. Try:

1. `I prefer Python for data work.`  → a **preference** node appears.
2. `Actually I prefer Rust now.`      → the Python node fades (dashed,
   superseded) and a new Rust node links back to it.
3. Click any node → evidence quotes and the full history chain.

### Tests

```bash
cd backend
./.venv/Scripts/python -m pytest -q
```

## Live runs and the API key

Reproducing the paper needs **no key**. Only *new* live experiments or the
interactive app against a hosted model require credentials: create
`backend/.env` (see `backend/.env.example`) with your `NVIDIA_API_KEY` (or any
OpenAI-compatible `OPENAI_API_KEY` / `OPENAI_BASE_URL`). `backend/.env` is
git-ignored and must never be committed.

## Visual encoding (Cytoscape)

- **Colour** = dimension (see legend)
- **Size** = calibrated confidence
- **Opacity** = confidence (superseded nodes fade further)
- **Dashed border** = superseded
- **Dashed edge** = "supersedes" (new → previous)

## Configuration

Everything vendor-specific lives in `backend/config.yaml` and `backend/.env`
(see `.env.example`). Notable knobs:

- `pipeline.salience_threshold` — how aggressive the gate is
- `pipeline.calibration_prior` / `calibration_shrink` — confidence calibration
- `pipeline.reinforce_step` — confidence bump on re-observation
- `llm.models.{chat,salience,extraction,embedding}` — per-role models
