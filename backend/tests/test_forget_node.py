"""User-initiated 'forget this about me': erases a fact and its whole history,
attributes, evidence and relations — unlike the reversible END/supersede."""
from __future__ import annotations

from app.db import get_conn, now_iso
from app.update import apply_assertion, forget_node
from app.utils import new_id


def _chat() -> tuple[str, str]:
    uid, sid = new_id("user"), new_id("sess")
    with get_conn() as conn:
        conn.execute("INSERT INTO users (id, label, created_at) VALUES (?, ?, ?)", (uid, "t", now_iso()))
        conn.execute("INSERT INTO sessions (id, user_id, title, created_at) VALUES (?, ?, ?, ?)",
                     (sid, uid, "t", now_iso()))
    return uid, sid


def _a(dim, key, value, operation="ADD", quote=None, conf=0.95, replaces=None):
    return {"dimension": dim, "key": key, "value": value, "operation": operation, "subject": "self",
            "confidence": conf, "attributes": [], "quote": quote or value,
            "explicitness": "explicit", "replaces_value": replaces, "target_node": None}


def _count(table, **where):
    clause = " AND ".join(f"{k} = ?" for k in where)
    with get_conn() as conn:
        return conn.execute(f"SELECT COUNT(*) c FROM {table} WHERE {clause}",
                            tuple(where.values())).fetchone()["c"]


def test_forget_removes_node_and_evidence():
    uid, sid = _chat()
    with get_conn() as conn:
        ch = apply_assertion(conn, uid, sid, new_id("msg"),
                             _a("preference", "food", "momo", quote="i love momo"))
        node_id = ch["node_id"]
    assert _count("nodes", id=node_id) == 1
    assert _count("evidence", node_id=node_id) == 1

    with get_conn() as conn:
        res = forget_node(conn, uid, node_id)
    assert res["forgotten"] is True
    assert _count("nodes", id=node_id) == 0
    assert _count("evidence", node_id=node_id) == 0


def test_forget_takes_the_whole_history_chain():
    uid, sid = _chat()
    # A SINGLE-slot fact that changes supersedes the old node, linking a chain.
    with get_conn() as conn:
        apply_assertion(conn, uid, sid, new_id("msg"), _a("identity", "name", "ujjwal", quote="my name is ujjwal"))
        ch2 = apply_assertion(conn, uid, sid, new_id("msg"),
                              _a("identity", "name", "apurva", quote="now call me apurva",
                                 replaces="ujjwal"))
        head = ch2["node_id"]
    assert _count("nodes", user_id=uid, dimension="identity", key="name") == 2

    with get_conn() as conn:
        res = forget_node(conn, uid, head)
    assert res["forgotten"] is True
    assert res["removed_nodes"] >= 2
    # Both the head and its superseded ancestor are gone.
    assert _count("nodes", user_id=uid, dimension="identity", key="name") == 0


def test_forget_unknown_node_is_not_found():
    uid, _ = _chat()
    with get_conn() as conn:
        res = forget_node(conn, uid, "nope")
    assert res["forgotten"] is False and res["reason"] == "not_found"
