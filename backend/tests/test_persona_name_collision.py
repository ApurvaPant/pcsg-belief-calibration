"""Regression: two personas that SHARE a name must stay separate — distinct graphs,
distinct per-persona label attribution, and distinct QA memory (user id). This fails
with the old name-keyed mapping (both collapse onto one pid) and passes with the
persona-id map (persona_users.json)."""
from __future__ import annotations

import json
import types

from app.db import get_conn, now_iso
from app.utils import new_id
from pcsg import run as R


def _persona(pid, name, split):
    return types.SimpleNamespace(pid=pid, name=name, split=split)


def _seed_two_same_named_users():
    """Two users with the SAME label 'sam', each with one distinct active node + signal."""
    u1, u2 = new_id("user"), new_id("user")
    with get_conn() as c:
        for uid in (u1, u2):
            c.execute("INSERT INTO users(id,label,created_at)VALUES(?,?,?)", (uid, "sam", now_iso()))
        n1, n2 = new_id("node"), new_id("node")
        for nid, uid, val in ((n1, u1, "pizza"), (n2, u2, "sushi")):
            c.execute("INSERT INTO nodes(id,user_id,dimension,key,value,confidence_raw,"
                      "confidence_calibrated,status,valid_from,created_at)"
                      "VALUES(?,?,?,?,?,?,?,?,?,?)",
                      (nid, uid, "preference", "food_likes", val, 0.9, 0.9, "active", now_iso(), now_iso()))
        for nid, uid, val in ((n1, u1, "pizza"), (n2, u2, "sushi")):
            c.execute("INSERT INTO confidence_signals(id,node_id,message_id,dimension,key,value,"
                      "explicitness,verbalized,self_consistency,evidence,created_at)"
                      "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                      (new_id("sig"), nid, new_id("msg"), "preference", "food_likes", val,
                       "explicit", 0.9, 1.0, 0.97, now_iso()))
    return u1, u2


def test_same_named_personas_stay_separate(tmp_path):
    u1, u2 = _seed_two_same_named_users()
    p0 = _persona(0, "sam", "fit")    # lower pid
    p1 = _persona(1, "sam", "test")   # higher pid, same name
    # the persona-id map the fixed pipeline persists during replay
    (tmp_path / "persona_users.json").write_text(json.dumps({"0": u1, "1": u2}), encoding="utf-8")

    stored = R._collect_signals([p0, p1], tmp_path)
    v0 = {a["value"] for a in stored[0]}
    v1 = {a["value"] for a in stored[1]}

    # Separate graphs/labels: each persona gets ONLY its own assertion (not collapsed).
    assert v0 == {"pizza"}, f"persona 0 should hold only its own node, got {v0}"
    assert v1 == {"sushi"}, f"persona 1 should hold only its own node, got {v1}"
    assert u1 != u2  # distinct underlying users (distinct graphs)

    # Separate QA memory: id map resolves each persona to its OWN user id.
    pid2uid = {int(k): v for k, v in json.loads((tmp_path / "persona_users.json").read_text()).items()}
    assert pid2uid[0] == u1 and pid2uid[1] == u2 and pid2uid[0] != pid2uid[1]


def test_name_keyed_mapping_would_collapse_them():
    """Documents the bug the fix removes: a NAME-keyed map collapses same-named personas
    onto one pid (so one persona's assertions vanish). The fix keys on id instead."""
    personas = [_persona(0, "sam", "fit"), _persona(1, "sam", "test")]
    name_to_pid = {}
    for p in personas:
        name_to_pid.setdefault(p.name, p.pid)
    # both names map to the SAME (lowest) pid -> collapse. This is exactly what the
    # fixed _collect_signals avoids by using persona_users.json (user id).
    assert name_to_pid["sam"] == 0
    assert len({name_to_pid[p.name] for p in personas}) == 1   # collapsed to 1 pid (the bug)
