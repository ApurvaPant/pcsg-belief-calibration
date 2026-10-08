"""PCSG pipeline tests (mock provider): cardinality, operations, temporality,
and user-scoped multi-session memory."""
from __future__ import annotations

from app import graph as graph_mod
from app import pipeline
from app.db import get_conn, now_iso
from app.ontology import STATUS_ACTIVE, STATUS_SUPERSEDED, DETAIL_ENDED
from app.update import calibrate, noisy_or
from app.utils import new_id


def _new_chat() -> tuple[str, str]:
    """Create an isolated (user_id, session_id) so tests don't share a graph."""
    uid, sid = new_id("user"), new_id("sess")
    with get_conn() as conn:
        conn.execute("INSERT INTO users (id, label, created_at) VALUES (?, ?, ?)",
                     (uid, "test", now_iso()))
        conn.execute("INSERT INTO sessions (id, user_id, title, created_at) VALUES (?, ?, ?, ?)",
                     (sid, uid, "test", now_iso()))
    return uid, sid


def _active(nodes):
    return [n for n in nodes if n["data"]["status"] == STATUS_ACTIVE]


def _by_value(nodes, value):
    return [n for n in nodes if n["data"]["value"] == value]


# ── confidence maths ────────────────────────────────────────────────────── #
def test_calibration_shrinks_toward_prior():
    assert calibrate(1.0) == 0.9
    assert calibrate(0.5) == 0.5
    assert calibrate(0.0) == 0.1


def test_noisy_or_increases_with_evidence():
    assert noisy_or(0.9, 0.9) == 0.99
    assert noisy_or(0.5, 0.0) == 0.5


# ── gate ──────────────────────────────────────────────────────────────────#
def test_pleasantry_is_not_salient():
    uid, sid = _new_chat()
    r = pipeline.process_turn("thanks, that's great!", sid, uid)
    assert r["salient"] is False
    assert r["changes"] == []


# ── THE failing sequence from the spec ─────────────────────────────────────#
def test_three_unrelated_loves_coexist():
    uid, sid = _new_chat()
    pipeline.process_turn("i love machine learning", sid, uid)
    pipeline.process_turn("i love jogging", sid, uid)
    pipeline.process_turn("i love momos", sid, uid)

    active = _active(graph_mod.build_graph(uid)["nodes"])
    assert len(active) == 3
    slots = {(n["data"]["dimension"], n["data"]["key"]) for n in active}
    assert ("interest", "topic") in slots
    assert ("habit", "jogging") in slots
    assert ("preference", "food_likes") in slots


def test_end_closes_only_the_matching_node():
    uid, sid = _new_chat()
    pipeline.process_turn("i love machine learning", sid, uid)
    pipeline.process_turn("i love jogging", sid, uid)
    pipeline.process_turn("i love momos", sid, uid)

    end = pipeline.process_turn("i stopped doing jogging", sid, uid)
    assert end["changes"][0]["action"] == "ended"

    nodes = graph_mod.build_graph(uid)["nodes"]
    assert len(_active(nodes)) == 2
    jogging = _by_value(nodes, "jogging")[0]["data"]
    assert jogging["status"] == STATUS_SUPERSEDED
    assert jogging["status_detail"] == DETAIL_ENDED


def test_end_with_no_match_is_ignored():
    uid, sid = _new_chat()
    r = pipeline.process_turn("i stopped doing jogging", sid, uid)
    assert r["changes"][0]["action"] == "ignored"
    assert graph_mod.build_graph(uid)["nodes"] == []


# ── SINGLE slot supersession ───────────────────────────────────────────────#
def test_single_slot_diet_supersedes_with_history():
    uid, sid = _new_chat()
    pipeline.process_turn("i'm vegetarian", sid, uid)
    r2 = pipeline.process_turn("i eat meat now", sid, uid)
    assert r2["changes"][0]["action"] == "superseded"

    nodes = graph_mod.build_graph(uid)["nodes"]
    assert len(nodes) == 2
    active = _active(nodes)[0]["data"]
    assert active["value"] == "eats meat"
    assert active["previous_node_id"] is not None
    assert len(graph_mod.get_node_detail(active["id"])["history"]) == 2


# ── MULTI reinforcement ─────────────────────────────────────────────────── #
def test_multi_reinforcement_bumps_confidence_no_new_node():
    uid, sid = _new_chat()
    r = pipeline.process_turn("i love momos", sid, uid)
    nid = r["changes"][0]["node_id"]
    before = graph_mod.get_node_detail(nid)["confidence_calibrated"]

    r2 = pipeline.process_turn("i love momos", sid, uid)
    assert r2["changes"][0]["action"] == "reinforced"
    assert graph_mod.get_node_detail(nid)["confidence_calibrated"] > before
    assert len(graph_mod.build_graph(uid)["nodes"]) == 1


# ── explicit replacement ───────────────────────────────────────────────────#
def test_explicit_replacement_links_old_and_new():
    uid, sid = _new_chat()
    pipeline.process_turn("i love python", sid, uid)
    r2 = pipeline.process_turn("I switched from Python to Rust", sid, uid)
    assert r2["changes"][0]["action"] == "superseded"

    active = _active(graph_mod.build_graph(uid)["nodes"])
    assert len(active) == 1
    assert active[0]["data"]["value"] == "rust"
    assert active[0]["data"]["previous_node_id"] is not None


# ── multi-session: state carries across sessions under one user ─────────────#
def test_state_carries_across_sessions():
    uid, sid1 = _new_chat()
    pipeline.process_turn("i love machine learning", sid1, uid)

    # A second session for the SAME user.
    with get_conn() as conn:
        sid2 = pipeline.create_session(conn, uid, "second chat")

    # Same fact again in the new session -> reinforces, does not duplicate.
    r = pipeline.process_turn("i love machine learning", sid2, uid)
    assert r["changes"][0]["action"] == "reinforced"

    nodes = graph_mod.build_graph(uid)["nodes"]
    assert len(nodes) == 1  # one user graph, shared across sessions

    # Evidence is attributed to both sessions.
    detail = graph_mod.get_node_detail(nodes[0]["data"]["id"])
    sess_ids = {e["session_id"] for e in detail["evidence"]}
    assert sess_ids == {sid1, sid2}
