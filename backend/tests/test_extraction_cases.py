"""PART 10 — the labelled regression set, run against the mock provider."""
from __future__ import annotations

import json
import pathlib

import pytest

from app import graph as graph_mod
from app import pipeline
from app.db import get_conn, now_iso
from app.utils import new_id

CASES = json.loads((pathlib.Path(__file__).parent / "extraction_cases.json").read_text())


def _new_user():
    uid, sid = new_id("user"), new_id("sess")
    with get_conn() as conn:
        conn.execute("INSERT INTO users (id, label, created_at) VALUES (?, ?, ?)", (uid, "t", now_iso()))
        conn.execute("INSERT INTO sessions (id, user_id, title, created_at) VALUES (?, ?, ?, ?)",
                     (sid, uid, "t", now_iso()))
    return uid, sid


def _active(uid):
    return [n["data"] for n in graph_mod.build_graph(uid)["nodes"] if n["data"]["status"] == "active"]


def _node(uid, dim, key, value=None):
    for d in _active(uid):
        if d["dimension"] == dim and d["key"] == key and (value is None or d["value"] == value):
            return d
    return None


def _attr(node_id, name, value=None):
    det = graph_mod.get_node_detail(node_id)
    for a in det["attributes"]:
        if a["status"] == "active" and a["name"] == name and (value is None or str(a["value"]) == str(value)):
            return a
    return None


@pytest.mark.parametrize("case", CASES, ids=[c["message"][:30] for c in CASES])
def test_case(case):
    uid, sid = _new_user()
    for msg in case.get("setup", []):
        pipeline.process_turn(msg, sid, uid)
    before = len(_active(uid))

    pipeline.process_turn(case["message"], sid, uid)
    exp = case["expect"]
    kind = exp["kind"]

    if kind == "none":
        assert len(_active(uid)) == before, f"{case['message']!r} created a node"

    elif kind == "node":
        n = _node(uid, exp["dimension"], exp["key"], exp.get("value"))
        assert n is not None, f"missing node {exp['dimension']}/{exp['key']}={exp.get('value')}"
        if "edges" in exp:
            assert len(graph_mod.build_graph(uid)["edges"]) == exp["edges"]

    elif kind == "attribute":
        n = _node(uid, exp["node_dimension"], exp["node_key"], exp.get("value"))
        assert n is not None, f"missing host node {exp['node_dimension']}/{exp['node_key']}"
        assert _attr(n["id"], exp["attr"], exp["attr_value"]) is not None, \
            f"missing attribute {exp['attr']}={exp['attr_value']}"

    elif kind == "edge":
        with get_conn() as conn:
            rows = conn.execute("SELECT relation FROM edges WHERE user_id = ?", (uid,)).fetchall()
        assert any(r["relation"] == exp["relation"] for r in rows), "missing edge"

    elif kind == "end":
        assert _node(uid, exp["dimension"], exp["key"], exp.get("value")) is None, "node still active"
        allnodes = [n["data"] for n in graph_mod.build_graph(uid)["nodes"]]
        ended = [d for d in allnodes if d["dimension"] == exp["dimension"] and d["status"] != "active"]
        assert ended, "no superseded/ended node"

    elif kind == "supersede":
        n = _node(uid, exp["dimension"], exp["key"], exp["value"])
        assert n is not None and n["previous_node_id"] is not None, "supersession/history missing"
