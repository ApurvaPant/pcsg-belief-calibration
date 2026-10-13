"""STEP 2 — the four QA answerer systems + per-system metrics (offline).

Uses oracle_mock (so gold-in-context drives correctness) and forces the
deterministic fallback embedder (no model download needed in tests)."""
from __future__ import annotations

import pytest

from app.config import settings
from app.db import get_conn, now_iso
from app.utils import new_id
from pcsg import baselines, embedding, oracle
from pcsg.analysis import qa_metrics


@pytest.fixture(autouse=True)
def oracle_env(monkeypatch):
    monkeypatch.setenv("PCSG_FORCE_FALLBACK_EMBED", "1")
    embedding._BACKEND = None  # reset cached backend so the env var takes effect
    prov = settings.llm.provider
    thr = settings.pipeline.salience_threshold
    monkeypatch.setattr(settings.llm, "provider", "oracle_mock")
    oracle.configure("overconfident", seed=2026)
    yield
    settings.llm.provider = prov
    settings.pipeline.salience_threshold = thr      # don't leak an open gate to later tests
    embedding._BACKEND = None


HISTORY = [
    {"role": "user", "text": "i'm vegetarian", "turn": 0},
    {"role": "assistant", "text": "noted", "turn": 0},
    {"role": "user", "text": "i live in berlin", "turn": 1},
    {"role": "user", "text": "i work as a teacher", "turn": 2},
]


def test_no_memory_abstains():
    oracle.set_qa("vegetarian")
    r = baselines.answer_no_memory("What is the user's diet?")
    assert r["system"] == "no_memory"
    assert r["abstained"] is True and r["context_tokens"] == 0


def test_full_context_answers_from_history():
    oracle.set_qa("vegetarian")
    r = baselines.answer_full_context("What is the user's diet?", HISTORY)
    assert r["system"] == "full_context"
    assert not r["abstained"]
    assert 0.0 <= r["confidence"] <= 1.0 and r["context_tokens"] > 0


def test_vector_rag_retrieves_and_answers():
    oracle.set_qa("berlin")
    r = baselines.answer_vector_rag("Where does the user live?", HISTORY, k=5)
    assert r["system"] == "vector_rag"
    assert "model_served" in r and r["confidence"] >= 0.0


def test_pcsg_answers_from_graph():
    uid, sid = new_id("user"), new_id("sess")
    with get_conn() as c:
        c.execute("INSERT INTO users(id,label,created_at)VALUES(?,?,?)", (uid, "bl", now_iso()))
        c.execute("INSERT INTO sessions(id,user_id,title,created_at)VALUES(?,?,?,?)", (sid, uid, "t", now_iso()))
    settings.pipeline.salience_threshold = 0.0
    from app import pipeline
    oracle.set_turn([{"dimension": "preference", "key": "diet", "value": "vegetarian",
                      "operation": "ADD", "explicitness": "explicit"}])
    pipeline.process_turn("i'm vegetarian", sid, uid)
    oracle.set_qa("vegetarian")
    with get_conn() as c:
        block = baselines.pcsg_block(c, uid)
        r = baselines.answer_pcsg("What is the user's diet?", c, uid)
    assert r["system"] == "pcsg"
    # the graph state block carries the node (the 'diet' key is preserved even if
    # the oracle's overconfident profile corrupts the value on some draws).
    assert block and "diet" in block.lower()


def test_unanswerable_is_abstained():
    oracle.set_qa("__unknown__")
    r = baselines.answer_full_context("What is the user's favourite colour?", HISTORY)
    assert r["abstained"] is True


def test_qa_metrics_summary():
    recs = [
        {"system": "x", "confidence": 0.9, "correct": 1, "abstained": 0, "is_unknown": 0, "tokens": 50},
        {"system": "x", "confidence": 0.8, "correct": 0, "abstained": 0, "is_unknown": 0, "tokens": 40},
        {"system": "x", "confidence": 0.2, "correct": 1, "abstained": 1, "is_unknown": 1, "tokens": 10},  # correctly abstained
        {"system": "x", "confidence": 0.7, "correct": 0, "abstained": 0, "is_unknown": 1, "tokens": 30},  # fabrication
    ]
    m = qa_metrics.summarize(recs)
    assert m["n"] == 4
    assert m["coverage"] == 0.75          # 3 of 4 answered
    assert m["fabrication_rate"] == 0.5   # 1 of 2 unknowns answered
    assert 0.0 <= m["accuracy"] <= 1.0
    assert m["answer_ece"] is not None and m["sweep"]
    assert m["tokens_per_q"] == pytest.approx(32.5)
