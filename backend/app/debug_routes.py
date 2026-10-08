"""Test-mode debug endpoints (§1.1 of the testing spec).

Mounted ONLY when PCSG_TEST_MODE=1. They add no production behaviour: they
reset/seed/snapshot the DB, inject a fixed assistant turn (so question-answer
chains are deterministic), wait for extraction (a no-op here because the
pipeline is synchronous — the chat reply is produced after reconciliation), and
arm fault injection in llm.py. No endpoint changes how extraction works.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from . import db, llm, pipeline
from .db import get_conn, now_iso
from .utils import new_id

router = APIRouter(prefix="/debug", tags=["debug"])

# Tables dumped by /snapshot and cleared by /reset, in FK-safe delete order.
_SNAPSHOT_TABLES = [
    "nodes", "attributes", "edges", "evidence", "events", "gate_log",
    "extraction_log", "pending_slots", "pending_confirmation", "messages",
    "sessions", "users",
]
_DELETE_ORDER = [
    "evidence", "attributes", "edges", "events", "gate_log", "extraction_log",
    "pending_slots", "pending_confirmation", "nodes", "messages", "auth_tokens",
    "sessions", "users",
]


@router.post("/reset")
def reset() -> dict:
    """Wipe every row (keep the schema) so each scenario starts clean."""
    db.init_db()
    with get_conn() as conn:
        conn.execute("PRAGMA foreign_keys = OFF")
        for t in _DELETE_ORDER:
            conn.execute(f"DELETE FROM {t}")
        conn.execute("PRAGMA foreign_keys = ON")
    llm.set_fault(None)
    return {"reset": True}


@router.post("/seed")
def seed(fixture: dict) -> dict:
    """Load a graph fixture directly (read-path isolation, §5).

    Shape: {user:{id,label?}, nodes:[...], attributes:[...], edges:[...],
    evidence:[...]}. Missing timestamps/confidence default sensibly. A node may
    carry an inline `attributes:[...]` list as a convenience.
    """
    counts = {"nodes": 0, "attributes": 0, "edges": 0, "evidence": 0}
    u = fixture.get("user") or {}
    uid = u.get("id") or db.DEFAULT_USER_ID
    sid = fixture.get("session_id") or new_id("sess")
    with get_conn() as conn:
        conn.execute("INSERT OR IGNORE INTO users (id, label, created_at) VALUES (?, ?, ?)",
                     (uid, u.get("label", "You"), now_iso()))
        conn.execute("INSERT OR IGNORE INTO sessions (id, user_id, title, created_at) VALUES (?, ?, ?, ?)",
                     (sid, uid, "seed", now_iso()))
        for n in fixture.get("nodes", []):
            nid = n.get("id") or new_id("node")
            raw = float(n.get("confidence_raw", n.get("confidence", 0.9)))
            cal = float(n.get("confidence_calibrated", raw))
            conn.execute(
                "INSERT INTO nodes (id, user_id, dimension, key, value, confidence_raw, "
                "confidence_calibrated, valid_from, valid_to, status, status_detail, "
                "previous_node_id, model, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (nid, uid, n["dimension"], n["key"], n["value"], raw, cal, now_iso(),
                 n.get("valid_to"), n.get("status", "active"),
                 n.get("status_detail", "active"), n.get("previous_node_id"),
                 n.get("model", "seed"), now_iso()))
            conn.execute("INSERT INTO evidence (id, node_id, session_id, message_id, quote, created_at) "
                         "VALUES (?,?,?,?,?,?)",
                         (new_id("ev"), nid, sid, new_id("msg"),
                          n.get("quote", f"seed: {n['value']}"), now_iso()))
            counts["nodes"] += 1
            counts["evidence"] += 1
            for a in n.get("attributes", []):
                aid = new_id("attr")
                conn.execute(
                    "INSERT INTO attributes (id, node_id, name, value, unit, confidence_raw, "
                    "confidence_calibrated, valid_from, status, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (aid, nid, a["name"], a["value"], a.get("unit"),
                     float(a.get("confidence_raw", 0.9)), float(a.get("confidence_calibrated", 0.9)),
                     now_iso(), a.get("status", "active"), now_iso()))
                conn.execute("INSERT INTO evidence (id, attribute_id, session_id, message_id, quote, created_at) "
                             "VALUES (?,?,?,?,?,?)",
                             (new_id("ev"), aid, sid, new_id("msg"),
                              a.get("quote", f"seed: {a['name']}={a['value']}"), now_iso()))
                counts["attributes"] += 1
        for e in fixture.get("edges", []):
            conn.execute(
                "INSERT INTO edges (id, user_id, source_node_id, relation, target_node_id, "
                "polarity, quote, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (new_id("edge"), uid, e["source_node_id"], e["relation"], e["target_node_id"],
                 e.get("polarity", "positive"), e.get("quote", "seed"), now_iso()))
            counts["edges"] += 1
    return {"seeded": True, "user_id": uid, "session_id": sid, "counts": counts}


@router.post("/wait")
def wait(message_id: str | None = None) -> dict:
    """No-op sync point: this pipeline reconciles synchronously before the chat
    reply returns, so there is no background extraction to await. Kept so the
    runner's contract is identical if the pipeline ever goes async."""
    return {"done": True, "synchronous": True, "message_id": message_id}


@router.get("/snapshot")
def snapshot() -> dict:
    """Full DB dump as JSON for invariant + assertion checking."""
    out: dict[str, Any] = {}
    with get_conn() as conn:
        for t in _SNAPSHOT_TABLES:
            rows = conn.execute(f"SELECT * FROM {t}").fetchall()
            out[t] = [dict(r) for r in rows]
    return out


@router.post("/inject_assistant")
def inject_assistant(body: dict) -> dict:
    """Append a fixed assistant message WITHOUT calling the model, then run the
    same post-reply processing a real turn does — pending-slot detection against
    the most recent turn's changes (so an injected question opens a slot)."""
    session_id = body["session_id"]
    content = body["content"]
    with get_conn() as conn:
        mid = pipeline._insert_message(conn, session_id, "assistant", content)
        # Reconstruct the changes from the last user message's events so the same
        # slot-detection logic runs as in a live turn.
        last_user = conn.execute(
            "SELECT id FROM messages WHERE session_id = ? AND role = 'user' "
            "ORDER BY created_at DESC LIMIT 1", (session_id,)).fetchone()
        changes = []
        if last_user:
            for r in conn.execute(
                "SELECT action, dimension, key, value, node_id FROM events WHERE message_id = ?",
                (last_user["id"],)):
                changes.append({"action": r["action"], "dimension": r["dimension"],
                                "key": r["key"], "value": r["value"], "node_id": r["node_id"]})
        det = pipeline._detect_slot(content, changes)
        if det:
            pipeline._open_slot(conn, session_id, det["node_id"], det["attribute_name"],
                                mid, det["slot_type"], det["topic"])
    return {"injected": True, "message_id": mid, "opened_slot": bool(det)}


@router.post("/fault")
def fault(body: dict) -> dict:
    """Arm a completion fault for the next N calls (see llm.set_fault)."""
    mode = body.get("mode")
    count = int(body.get("count", 1))
    llm.set_fault(mode, count)
    return {"armed": mode, "count": count if mode else 0}
