"""Guard for KI-1: the read path must not fabricate absent attribute values."""
from __future__ import annotations

import re

from app import pipeline
from app.db import get_conn, now_iso
from app.utils import new_id

_DURATION = re.compile(r"\b\d+\s*(year|years|month|months|week|weeks|day|days)\b", re.I)


def _new_chat():
    uid, sid = new_id("user"), new_id("sess")
    with get_conn() as conn:
        conn.execute("INSERT INTO users (id, label, created_at) VALUES (?, ?, ?)", (uid, "t", now_iso()))
        conn.execute("INSERT INTO sessions (id, user_id, title, created_at) VALUES (?, ?, ?, ?)",
                     (sid, uid, "t", now_iso()))
    return uid, sid


def test_profile_has_no_duration_for_node_without_one():
    uid, sid = _new_chat()
    pipeline.process_turn("i love momo as a food", sid, uid)  # no duration attribute
    with get_conn() as conn:
        profile = pipeline._profile_summary(conn, uid, historical=True)
    assert "momo" in profile
    # the momo line must not carry a fabricated duration
    momo_line = next((ln for ln in profile.splitlines() if "momo" in ln), "")
    assert "duration" not in momo_line


def test_reply_does_not_fabricate_duration():
    uid, sid = _new_chat()
    pipeline.process_turn("i love momo as a food", sid, uid)
    r = pipeline.process_turn("how long have i liked momo?", sid, uid)
    assert not _DURATION.search(r["reply"]), f"reply fabricated a duration: {r['reply']!r}"
