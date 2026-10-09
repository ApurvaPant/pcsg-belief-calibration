"""§6 fault injection / resilience: bounded retries, graceful chat degradation,
no partial writes, and failed-extraction logging."""
from __future__ import annotations

import httpx
import pytest

from app import extraction, llm, pipeline
from app.db import get_conn, now_iso
from app.utils import new_id


def _chat():
    uid, sid = new_id("user"), new_id("sess")
    with get_conn() as conn:
        conn.execute("INSERT INTO users (id, label, created_at) VALUES (?, ?, ?)", (uid, "t", now_iso()))
        conn.execute("INSERT INTO sessions (id, user_id, title, created_at) VALUES (?, ?, ?, ?)",
                     (sid, uid, "t", now_iso()))
    return uid, sid


def test_transient_fault_is_retried_then_succeeds():
    llm.RETRY_LOG.clear()
    llm.set_fault("http_429", 2)          # fail twice, then succeed
    r = llm.complete([{"role": "user", "content": "hi"}], model="m")
    assert r.get("text") is not None       # recovered (mock) after retries
    assert len(llm.RETRY_LOG) == 2
    llm.set_fault(None)


def test_gives_up_after_max_retries():
    llm.RETRY_LOG.clear()
    llm.set_fault("http_500", 50)          # always fails
    with pytest.raises(httpx.HTTPStatusError):
        llm.complete([{"role": "user", "content": "hi"}], model="m")
    assert len(llm.RETRY_LOG) == llm._MAX_RETRIES
    llm.set_fault(None)


def test_chat_failure_degrades_gracefully_and_keeps_writes():
    uid, _ = _chat()
    llm.set_fault("http_500", 50)          # every model call fails (incl. chat)
    try:
        r = pipeline.process_turn("i love pizza", None, uid)
    finally:
        llm.set_fault(None)
    assert r["reply"]                       # a reply was still returned (no 500)
    # the mock write path ran before the chat call, so the node is intact
    with get_conn() as conn:
        n = conn.execute("SELECT COUNT(*) c FROM nodes WHERE user_id = ? AND value = 'pizza'",
                         (uid,)).fetchone()["c"]
    assert n == 1


def test_failed_extraction_is_logged_and_writes_nothing():
    uid, sid = _chat()
    mid = new_id("msg")
    ext = extraction.ExtractionResult([], [], "m", "<fill error: boom>",
                                      failed=True, error="fill:HTTPStatusError")
    with get_conn() as conn:
        conn.execute("INSERT INTO messages (id, session_id, role, content, created_at) "
                     "VALUES (?, ?, 'user', 'x', ?)", (mid, sid, now_iso()))
        pipeline._log_extraction(conn, mid, [], ext, accepted=False, reason_code=None)
        row = conn.execute("SELECT status, reason_code FROM extraction_log WHERE message_id = ?",
                           (mid,)).fetchone()
        nodes = conn.execute("SELECT COUNT(*) c FROM nodes WHERE user_id = ?", (uid,)).fetchone()["c"]
    assert row["status"] == "failed"
    assert "fill" in (row["reason_code"] or "")
    assert nodes == 0                       # a failed extraction leaves the graph unchanged
