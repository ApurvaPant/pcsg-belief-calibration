# CLAUDE.md — User-State Graph Chat

Guidance for working in this repo. See `README.md` for run instructions.

## What this is

A chat interface that builds a **temporal user-state graph**. Nodes are typed
assertions about the USER (not a general entity graph). It is evidence-backed
and non-destructive: contradictions supersede rather than overwrite.

## Hard constraints (do not violate)

- **Two processes only.** Frontend (React + Vite + TS) on **5173**; backend
  (Python + FastAPI) on **8000**; CORS enabled between them.
- **Storage is SQLite.** No Neo4j, no vector DB.
- **Graph viz is Cytoscape.js.**
- **`backend/app/llm.py` is the ONLY module allowed to make model network
  calls.** It is provider-agnostic: an OpenAI-compatible `httpx` client (no
  vendor SDK) reading `OPENAI_BASE_URL`, `OPENAI_API_KEY`, and model names from
  `config.yaml` / `.env`. It exposes exactly:
  - `complete(messages, model, temperature, max_tokens) -> {text, prompt_tokens, completion_tokens, latency_ms}`
  - `embed(texts, model) -> vectors`
  It includes a `mock` provider (`LLM_PROVIDER=mock`) returning deterministic
  canned extractions so the pipeline and tests run with no key and no cost.

## Node model

Dimensions: `preference, goal, skill, project, habit, belief, relationship,
communication_style`.

Each node: `value, confidence_raw, confidence_calibrated, valid_from, valid_to,
status (active|superseded|deleted), evidence (pointers to exact utterances),
previous_node_id`.

**Temporal rule:** on contradiction, close `valid_to` on the old node, set
`status=superseded`, insert a new node with `previous_node_id` pointing at it.
Both stay in the graph. Superseded nodes render dashed + faded.

**Visual encoding:** colour = dimension, size/opacity = confidence, dashed =
superseded. Clicking a node shows evidence quotes + full history chain.

**Salience gate:** not every message produces nodes; a cheap gate filters most
turns out before extraction runs.

## Layout

```
backend/app/
  config.py       config.yaml + .env loader (singleton `settings`)
  llm.py          SOLE network module; complete()/embed() + mock provider
  db.py           SQLite schema + transactional get_conn()
  salience.py     salience gate  ([[TASK:SALIENCE]] prompt tag)
  extraction.py   assertion extraction ([[TASK:EXTRACTION]] prompt tag)
  reconcile.py    temporal reconciliation + confidence calibration
  pipeline.py     orchestrates a turn: salience → extract → reply → persist
  graph.py        Cytoscape payload + node detail (evidence + history)
  main.py         FastAPI routes + CORS
frontend/src/
  App.tsx, components/{ChatPanel,GraphPanel,NodeDetail}.tsx, api.ts, theme.ts
```

The mock provider routes on a `[[TASK:...]]` tag embedded in the system prompt
by the prompt builders; real providers treat it as inert text. Keep
`theme.ts:DIMENSION_COLORS` in sync with `constants.py:DIMENSIONS`.

## Verify

`cd backend && ./.venv/Scripts/python -m pytest -q` (uses the mock provider).
