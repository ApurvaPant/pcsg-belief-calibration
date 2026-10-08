"""PART 1 — interrogatives never produce assertions."""
from __future__ import annotations

import pytest

from app import graph as graph_mod
from app import pipeline
from app.salience import classify_message
from app.db import get_conn, now_iso
from app.utils import new_id

PURE_QUERIES = ["what is my name", "who is my girlfriend", "do you know what I like?",
                "what is my favorite food"]
MIXED = "my name is apurva, what's yours?"


def _new_user():
    uid, sid = new_id("user"), new_id("sess")
    with get_conn() as conn:
        conn.execute("INSERT INTO users (id, label, created_at) VALUES (?, ?, ?)", (uid, "t", now_iso()))
        conn.execute("INSERT INTO sessions (id, user_id, title, created_at) VALUES (?, ?, ?, ?)",
                     (sid, uid, "t", now_iso()))
    return uid, sid


@pytest.mark.parametrize("q", PURE_QUERIES)
def test_pure_query_classified(q):
    is_query, extract_text = classify_message(q)
    assert is_query and extract_text == ""


@pytest.mark.parametrize("q", PURE_QUERIES)
def test_pure_query_produces_no_assertions(q):
    uid, sid = _new_user()
    r = pipeline.process_turn(q, sid, uid)
    assert r["salient"] is False
    assert graph_mod.build_graph(uid)["nodes"] == []
    assert any(c["action"] == "retrieval_query" for c in r["changes"])


def test_mixed_extracts_only_declarative():
    is_query, extract_text = classify_message(MIXED)
    assert is_query is False
    assert "apurva" in extract_text and "yours" not in extract_text.lower()
