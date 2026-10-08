"""Auth: hashing, register/login, token resolution, per-user isolation."""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app import auth, pipeline
from app import graph as graph_mod
from app.db import get_conn
from app.utils import new_id


def test_password_hash_roundtrip():
    h = auth.hash_password("hunter2!")
    assert h.startswith("pbkdf2_sha256$")
    assert auth.verify_password("hunter2!", h)
    assert not auth.verify_password("wrong", h)
    assert not auth.verify_password("hunter2!", None)


def test_register_login_and_token():
    uname = "user_" + new_id("x")[-8:]
    reg = auth.register(uname, "secret123")
    assert reg["user"]["username"] == uname
    token = reg["token"]

    with get_conn() as conn:
        assert auth.user_id_for_token(conn, token) == reg["user"]["id"]

    got = auth.login(uname, "secret123")
    assert got["user"]["id"] == reg["user"]["id"]


def test_duplicate_username_rejected():
    uname = "dupe_" + new_id("x")[-8:]
    auth.register(uname, "secret123")
    with pytest.raises(HTTPException) as ei:
        auth.register(uname, "secret123")
    assert ei.value.status_code == 409


def test_bad_login_rejected():
    uname = "bad_" + new_id("x")[-8:]
    auth.register(uname, "secret123")
    with pytest.raises(HTTPException) as ei:
        auth.login(uname, "nope")
    assert ei.value.status_code == 401


def test_graphs_are_isolated_per_user():
    a = auth.register("alice_" + new_id("x")[-6:], "secret123")["user"]["id"]
    b = auth.register("bob_" + new_id("x")[-6:], "secret123")["user"]["id"]

    with get_conn() as conn:
        sa = pipeline.create_session(conn, a, "a")
        sb = pipeline.create_session(conn, b, "b")

    pipeline.process_turn("i love momos", sa, a)
    pipeline.process_turn("i love sushi", sb, b)

    a_vals = {n["data"]["value"] for n in graph_mod.build_graph(a)["nodes"]}
    b_vals = {n["data"]["value"] for n in graph_mod.build_graph(b)["nodes"]}
    assert a_vals == {"momos"}
    assert b_vals == {"sushi"}
