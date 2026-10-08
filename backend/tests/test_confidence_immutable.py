"""BUG-001 regression: reinforcement must never mutate confidence_raw (INV-01 /
the hard rule). Repeated evidence grows confidence_calibrated via Noisy-OR,
monotonically and strictly below 1.0; confidence_raw stays at first insert."""
from __future__ import annotations

from app.db import get_conn, now_iso
from app.update import apply_assertion
from app.utils import new_id


def _chat():
    uid, sid = new_id("user"), new_id("sess")
    with get_conn() as conn:
        conn.execute("INSERT INTO users (id, label, created_at) VALUES (?, ?, ?)", (uid, "t", now_iso()))
        conn.execute("INSERT INTO sessions (id, user_id, title, created_at) VALUES (?, ?, ?, ?)",
                     (sid, uid, "t", now_iso()))
    return uid, sid


def _a(value, conf=0.9):
    return {"dimension": "preference", "key": "food_likes", "value": value, "operation": "ADD",
            "subject": "self", "confidence": conf, "attributes": [], "quote": f"i love {value}",
            "explicitness": "explicit", "replaces_value": None, "target_node": None}


def _row(uid):
    with get_conn() as conn:
        return conn.execute(
            "SELECT confidence_raw, confidence_calibrated FROM nodes "
            "WHERE user_id = ? AND value = 'pizza'", (uid,)).fetchone()


def test_reinforce_keeps_raw_grows_calibrated_capped():
    uid, sid = _chat()
    with get_conn() as conn:
        apply_assertion(conn, uid, sid, new_id("msg"), _a("pizza"))
    first = _row(uid)
    for _ in range(4):
        with get_conn() as conn:
            apply_assertion(conn, uid, sid, new_id("msg"), _a("pizza"))
    after = _row(uid)
    assert after["confidence_raw"] == first["confidence_raw"], "confidence_raw must be immutable"
    assert after["confidence_calibrated"] >= first["confidence_calibrated"], "calibrated non-decreasing"
    assert after["confidence_calibrated"] < 1.0, "calibrated capped below 1"
