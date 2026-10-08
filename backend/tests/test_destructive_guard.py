"""The destructive-operation guard: a negation, a rewritten fragment, or a
low-confidence op must never END or supersede a correct node (critical bug)."""
from __future__ import annotations

from app.db import get_conn, now_iso
from app.update import apply_assertion
from app.utils import new_id


def _chat() -> tuple[str, str]:
    uid, sid = new_id("user"), new_id("sess")
    with get_conn() as conn:
        conn.execute("INSERT INTO users (id, label, created_at) VALUES (?, ?, ?)", (uid, "t", now_iso()))
        conn.execute("INSERT INTO sessions (id, user_id, title, created_at) VALUES (?, ?, ?, ?)",
                     (sid, uid, "t", now_iso()))
    return uid, sid


def _a(dim, key, value, operation="ADD", quote=None, conf=0.95):
    return {"dimension": dim, "key": key, "value": value, "operation": operation, "subject": "self",
            "confidence": conf, "attributes": [], "quote": quote or value,
            "explicitness": "explicit", "replaces_value": None, "target_node": None}


def _status(uid, dim, key, value):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT status FROM nodes WHERE user_id = ? AND dimension = ? AND key = ? AND value = ? "
            "ORDER BY created_at DESC LIMIT 1", (uid, dim, key, value)).fetchone()
    return row["status"] if row else None


def _pending(kind):
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) n FROM pending_confirmation WHERE kind = ? AND status = 'open'",
            (kind,)).fetchone()["n"]


def _seed(uid, sid, dim, key, value):
    with get_conn() as conn:
        apply_assertion(conn, uid, sid, new_id("msg"), _a(dim, key, value, quote=f"i do {value}"))


# ── the core bug: a negation must NOT end a node ────────────────────────────#
def test_negation_does_not_end_node():
    uid, sid = _chat()
    _seed(uid, sid, "relationship", "mom", "mom")
    before = _pending("end")
    with get_conn() as conn:
        ch = apply_assertion(conn, uid, sid, new_id("msg"),
                             _a("relationship", "mom", "mom", "END", quote="they don't get along", conf=0.95))
    assert ch["action"] == "ignored"
    assert ch["reason_code"] == "destructive_blocked"
    assert _status(uid, "relationship", "mom", "mom") == "active"   # mom survives
    assert _pending("end") == before + 1                           # queued for confirmation


def test_explicit_cessation_ends_node():
    uid, sid = _chat()
    _seed(uid, sid, "habit", "jogging", "jogging")
    with get_conn() as conn:
        ch = apply_assertion(conn, uid, sid, new_id("msg"),
                             _a("habit", "jogging", "jogging", "END", quote="i stopped jogging", conf=0.9))
    assert ch["action"] == "ended"
    assert _status(uid, "habit", "jogging", "jogging") == "superseded"


def test_fragment_rewrite_cannot_end_even_with_cessation():
    uid, sid = _chat()
    _seed(uid, sid, "habit", "running", "running")
    with get_conn() as conn:
        ch = apply_assertion(conn, uid, sid, new_id("msg"),
                             _a("habit", "running", "running", "END", quote="i stopped running", conf=0.95),
                             qa_resolved=True)
    assert ch["action"] == "ignored" and ch["reason_code"] == "destructive_blocked"
    assert _status(uid, "habit", "running", "running") == "active"


def test_low_confidence_end_is_blocked():
    uid, sid = _chat()
    _seed(uid, sid, "habit", "cycling", "cycling")
    with get_conn() as conn:
        ch = apply_assertion(conn, uid, sid, new_id("msg"),
                             _a("habit", "cycling", "cycling", "END", quote="i stopped cycling", conf=0.5))
    assert ch["action"] == "ignored" and ch["reason_code"] == "destructive_blocked"
    assert _status(uid, "habit", "cycling", "cycling") == "active"


def test_end_on_unnamed_target_is_blocked():
    uid, sid = _chat()
    _seed(uid, sid, "habit", "painting", "painting")
    with get_conn() as conn:  # cessation present but the quote never names painting
        ch = apply_assertion(conn, uid, sid, new_id("msg"),
                             _a("habit", "painting", "painting", "END", quote="i stopped doing it", conf=0.95))
    assert ch["action"] == "ignored" and ch["reason_code"] == "destructive_blocked"
    assert _status(uid, "habit", "painting", "painting") == "active"


def test_restore_reactivates_ended_node():
    from app import update
    uid, sid = _chat()
    _seed(uid, sid, "habit", "swimming", "swimming")
    with get_conn() as conn:
        ch = apply_assertion(conn, uid, sid, new_id("msg"),
                             _a("habit", "swimming", "swimming", "END", quote="i stopped swimming", conf=0.9))
        node_id = ch["node_id"]
    assert _status(uid, "habit", "swimming", "swimming") == "superseded"
    with get_conn() as conn:
        res = update.restore_node(conn, uid, node_id)
    assert res["restored"] is True
    assert _status(uid, "habit", "swimming", "swimming") == "active"
