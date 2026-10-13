"""STEP 2 — four answerer systems for the Tier-2 QA comparison (table T3).

All four share ONE answer prompt template (only the CONTEXT block differs), the
same answerer model role (`chat`), the same judge and the same token logging, so
differences are attributable to memory representation, not prompt wording:

  no_memory     — question only (measures guessing without memory).
  full_context  — the whole raw conversation history, with turn indices.
  vector_rag    — top-k=5 user messages by cosine similarity (local embeddings),
                  each shown with its turn index so recency is visible.
  pcsg          — the PCSG graph state block (active nodes + edges).

Each answerer returns {system, answer, confidence (0..1), abstained, tokens_in,
tokens_out, model, context_tokens}. Any system may answer "I don't know". The
answer confidence feeds risk–coverage / abstention / answer-level ECE uniformly.
All model calls go through app/llm.py.
"""
from __future__ import annotations

import json
import re
from typing import Any

from app import llm

ANSWER_SYSTEM = (
    "[[TASK:QA_ANSWER]]\n"
    "You answer a question about a user using ONLY the context provided. If the "
    "context does not contain the answer, reply exactly \"I don't know\". Respond "
    "with JSON: {\"answer\": <string>, \"confidence\": <number 0..1>}.\n"
    "CONTEXT:\n{context}"
)

_IDK = re.compile(r"^\s*(i\s*(do\s*n'?t|do not)\s*know|idk|no idea|not sure|no record)", re.I)


def _is_idk(answer: str) -> bool:
    return bool(_IDK.match(answer or ""))


def _parse(text: str) -> tuple[str, float]:
    try:
        obj = json.loads(re.search(r"\{.*\}", text, re.S).group(0))
        ans = str(obj.get("answer", "")).strip()
        conf = float(obj.get("confidence", 0.5))
        return ans or "I don't know", max(0.0, min(1.0, conf))
    except Exception:  # noqa: BLE001 — non-JSON reply: treat the text as the answer
        t = (text or "").strip()
        return (t or "I don't know"), (0.2 if _is_idk(t) else 0.5)


def _ask(context: str, question: str, model_role: str = "chat") -> dict[str, Any]:
    sys = ANSWER_SYSTEM.replace("{context}", context or "(no context)")
    msgs = [{"role": "system", "content": sys}, {"role": "user", "content": f"QUESTION: {question}"}]
    r = llm.complete(msgs, model=llm.resolve_model(model_role))
    ans, conf = _parse(r.get("text", ""))
    return {"answer": ans, "confidence": conf, "abstained": _is_idk(ans),
            "tokens_in": int(r.get("prompt_tokens", 0) or 0),
            "tokens_out": int(r.get("completion_tokens", 0) or 0),
            "model_requested": llm.resolve_model(model_role), "model_served": r.get("model"),
            "context_tokens": len((context or "").split())}


# ── context builders ──────────────────────────────────────────────────────── #
def full_context_block(history: list[dict]) -> str:
    """Render the entire raw conversation with turn indices (recency visible)."""
    lines = []
    for i, turn in enumerate(history):
        who = turn.get("role", "user")
        lines.append(f"[turn {i}] {who}: {turn.get('text', '')}")
    return "\n".join(lines)


def vector_rag_block(user_turns: list[dict], question: str, k: int = 5) -> str:
    """Top-k user messages by cosine similarity, each with its turn index."""
    from pcsg import embedding
    docs = [t.get("text", "") for t in user_turns]
    hits = embedding.top_k(question, docs, k=k)
    lines = []
    for idx, score in hits:
        t = user_turns[idx]
        lines.append(f"[turn {t.get('turn', idx)}] {t.get('text', '')}")
    return "\n".join(lines)


def pcsg_block(conn, user_id: str) -> str:
    from app import pipeline
    return pipeline._profile_summary(conn, user_id)


# ── system answerers ──────────────────────────────────────────────────────── #
def answer_no_memory(question: str) -> dict[str, Any]:
    out = _ask("", question); out["system"] = "no_memory"; return out


def answer_full_context(question: str, history: list[dict]) -> dict[str, Any]:
    out = _ask(full_context_block(history), question); out["system"] = "full_context"; return out


def answer_vector_rag(question: str, user_turns: list[dict], k: int = 5) -> dict[str, Any]:
    out = _ask(vector_rag_block(user_turns, question, k), question); out["system"] = "vector_rag"; return out


def answer_pcsg(question: str, conn, user_id: str) -> dict[str, Any]:
    out = _ask(pcsg_block(conn, user_id), question); out["system"] = "pcsg"; return out


SYSTEMS = ("no_memory", "full_context", "vector_rag", "pcsg")
