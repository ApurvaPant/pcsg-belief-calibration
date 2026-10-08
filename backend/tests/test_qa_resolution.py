"""Tests for the question–answer resolution fix (mock provider):
fragment resolution, every-question-opens-a-slot, refinement chains, and
cross-dimension duplicate handling."""
from __future__ import annotations

from app import graph as graph_mod
from app import pipeline
from app.db import get_conn, now_iso
from app.ontology import STATUS_ACTIVE
from app.update import apply_assertion
from app.utils import new_id


def _chat() -> tuple[str, str]:
    uid, sid = new_id("user"), new_id("sess")
    with get_conn() as conn:
        conn.execute("INSERT INTO users (id, label, created_at) VALUES (?, ?, ?)",
                     (uid, "test", now_iso()))
        conn.execute("INSERT INTO sessions (id, user_id, title, created_at) VALUES (?, ?, ?, ?)",
                     (sid, uid, "test", now_iso()))
    return uid, sid


def _assistant_asks(sid, text, node_id=None, attr=None, slot_type="open"):
    """Insert an assistant question and open the slot it implies."""
    with get_conn() as conn:
        amid = new_id("msg")
        conn.execute(
            "INSERT INTO messages (id, session_id, role, content, created_at) "
            "VALUES (?, ?, 'assistant', ?, ?)", (amid, sid, text, now_iso()))
        pipeline._open_slot(conn, sid, node_id, attr, amid, slot_type, text)
    return amid


def _active(nodes):
    return [n for n in nodes if n["data"]["status"] == STATUS_ACTIVE]


def _active_attrs(node_id) -> dict[str, str]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT name, value FROM attributes WHERE node_id = ? AND status = 'active'",
            (node_id,)).fetchall()
    return {r["name"]: r["value"] for r in rows}


def _assertion(dimension, key, value, operation="ADD", **kw):
    base = {"dimension": dimension, "key": key, "value": value, "operation": operation,
            "subject": "self", "confidence": 0.9, "attributes": [], "quote": value,
            "explicitness": "explicit", "replaces_value": None, "target_node": None}
    base.update(kw)
    return base


# ── fix step 1 + 2: a fragment answer to a question is resolved + extracted ──#
def test_fragment_answer_is_resolved_and_extracted():
    uid, sid = _chat()
    _assistant_asks(sid, "what do you do on weekends?")  # open slot, no node
    r = pipeline.process_turn("mostly hiking", sid, uid)
    assert r["salient"] is True
    made = {(c["action"], c["dimension"], c["value"]) for c in r["changes"]}
    assert ("created", "habit", "hiking") in made


def test_fragment_without_prior_question_is_not_salient():
    uid, sid = _chat()
    # No preceding assistant question -> "technical" stays a low-salience fragment.
    r = pipeline.process_turn("technical", sid, uid)
    assert r["salient"] is False


# ── fix step 2: every assistant question opens a slot ───────────────────────#
def test_detect_slot_classifies_question_types():
    changes = [{"action": "created", "node_id": "n1", "dimension": "habit",
                "key": "reading", "value": "reading"}]
    attr = pipeline._detect_slot("how long have you done that?", changes)
    assert attr and attr["slot_type"] == "attribute" and attr["attribute_name"] == "duration"
    openq = pipeline._detect_slot("what kind of books do you like?", changes)
    assert openq and openq["slot_type"] == "open" and openq["node_id"] == "n1"
    assert pipeline._detect_slot("that's lovely.", changes) is None


# ── fix step 3: refinement chain attaches to the node, no duplicate ─────────#
def test_open_slot_answer_refines_existing_node():
    uid, sid = _chat()
    r1 = pipeline.process_turn("i like reading", sid, uid)
    node_id = r1["changes"][0]["node_id"]
    _assistant_asks(sid, "what kind of books do you enjoy reading?", node_id=node_id, slot_type="open")

    r = pipeline.process_turn("technical", sid, uid)
    assert r["changes"][0]["action"] == "refined"
    assert r["changes"][0]["node_id"] == node_id
    assert len(_active(graph_mod.build_graph(uid)["nodes"])) == 1  # no new node
    assert _active_attrs(node_id).get("genre") == "technical"


def test_interest_answer_is_not_forced_into_an_attribute():
    uid, sid = _chat()
    r1 = pipeline.process_turn("i like reading", sid, uid)
    node_id = r1["changes"][0]["node_id"]
    _assistant_asks(sid, "what kind of books?", node_id=node_id, slot_type="open")
    # An explicit like is a new interest, not a qualifier on the reading node.
    r = pipeline.process_turn("i love sci-fi novels", sid, uid)
    assert all(c["action"] != "refined" or c["node_id"] != node_id
               or c.get("reason_code") is None for c in r["changes"])
    assert "genre" not in _active_attrs(node_id)


# ── fix step 4: cross-dimension duplicates ──────────────────────────────────#
def test_cross_dimension_duplicate_refines_existing():
    uid, sid = _chat()
    with get_conn() as conn:
        c1 = apply_assertion(conn, uid, sid, "m1", _assertion("habit", "reading", "reading"))
        c2 = apply_assertion(conn, uid, sid, "m2", _assertion("interest", "topic", "reading"))
    assert c1["action"] == "created"
    assert c2["action"] == "reinforced"
    assert c2["reason_code"] == "dimension_conflict"
    assert len(_active(graph_mod.build_graph(uid)["nodes"])) == 1


def test_language_skill_and_preference_coexist():
    uid, sid = _chat()
    with get_conn() as conn:
        apply_assertion(conn, uid, sid, "m1", _assertion("skill", "german", "german"))
        apply_assertion(conn, uid, sid, "m2", _assertion("preference", "tool_choice", "german"))
    # A language may be both a skill and a preference — both are kept.
    assert len(_active(graph_mod.build_graph(uid)["nodes"])) == 2
