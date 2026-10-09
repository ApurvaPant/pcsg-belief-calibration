"""STEP 0: per-assertion confidence signals are written append-only, carry the
verbalized + evidence signals always, and the self-consistency signal when
resampling is enabled."""
from __future__ import annotations

import os

from app import pipeline
from app.db import get_conn, now_iso
from app.utils import new_id


def _chat():
    uid, sid = new_id("user"), new_id("sess")
    with get_conn() as conn:
        conn.execute("INSERT INTO users (id, label, created_at) VALUES (?, ?, ?)", (uid, "t", now_iso()))
        conn.execute("INSERT INTO sessions (id, user_id, title, created_at) VALUES (?, ?, ?, ?)",
                     (sid, uid, "t", now_iso()))
    return uid, sid


def _signals(uid):
    with get_conn() as conn:
        return conn.execute(
            "SELECT cs.* FROM confidence_signals cs JOIN nodes n ON n.id = cs.node_id "
            "WHERE n.user_id = ?", (uid,)).fetchall()


def test_signals_written_with_verbalized_and_evidence():
    uid, sid = _chat()
    pipeline.process_turn("my name is kiran", sid, uid)
    rows = _signals(uid)
    assert len(rows) >= 1
    r = rows[0]
    assert 0.0 <= r["verbalized"] <= 1.0
    assert r["evidence"] >= r["verbalized"]          # Noisy-OR with a prior never lowers it
    assert r["self_consistency"] is None             # resampling disabled by default


def test_self_consistency_recorded_when_enabled():
    os.environ["PCSG_SELF_CONSISTENCY"] = "1"
    try:
        uid, sid = _chat()
        pipeline.process_turn("i love pizza", sid, uid)
    finally:
        os.environ.pop("PCSG_SELF_CONSISTENCY", None)
    rows = _signals(uid)
    assert rows and rows[0]["self_consistency"] == 1.0   # mock resamples agree deterministically


def test_signals_are_append_only_on_reinforce():
    uid, sid = _chat()
    pipeline.process_turn("i love pizza", sid, uid)
    pipeline.process_turn("i love pizza", sid, uid)   # reinforce -> a second signal row
    assert len(_signals(uid)) == 2
