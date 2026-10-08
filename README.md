# User-State Graph Chat

A chat interface that builds a **temporal, evidence-backed graph of the user**.
As you talk, the backend gates each turn for salience, extracts typed
assertions about you (preferences, goals, skills, projects, habits, beliefs,
relationships, communication style), and reconciles them over time. When you
contradict something you said earlier, the old belief is **not overwritten** —
it is closed off and superseded, and both versions stay in the graph.

## Architecture

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

## Running

### Backend

```bash
cd backend
py -3.13 -m venv .venv                 # Python 3.13 recommended
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

## Visual encoding (Cytoscape)

- **Colour** = dimension (see legend)
- **Size** = calibrated confidence
- **Opacity** = confidence (superseded nodes fade further)
- **Dashed border** = superseded
- **Dashed edge** = "supersedes" (new → previous)

## Configuration

Everything vendor-specific lives in `backend/config.yaml` and
`backend/.env` (see `.env.example`). Notable knobs:

- `pipeline.salience_threshold` — how aggressive the gate is
- `pipeline.calibration_prior` / `calibration_shrink` — confidence calibration
- `pipeline.reinforce_step` — confidence bump on re-observation
- `llm.models.{chat,salience,extraction,embedding}` — per-role models
