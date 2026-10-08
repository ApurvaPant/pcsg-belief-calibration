"""Authentication: username/password accounts with token-based sessions.

Passwords are hashed with PBKDF2-HMAC-SHA256 (stdlib — no extra dependency).
A successful register/login issues an opaque bearer token stored in
`auth_tokens`; the FastAPI dependency `current_user` resolves it to a user.
Each user owns a private PCSG graph (scoped by user_id everywhere else).
"""
from __future__ import annotations

import hashlib
import hmac
import secrets

from fastapi import Header, HTTPException

from .db import get_conn, now_iso
from .utils import new_id

_ITERATIONS = 200_000


# ── password hashing ────────────────────────────────────────────────────── #
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERATIONS)
    return f"pbkdf2_sha256${_ITERATIONS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        algo, iters, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(iters))
        return hmac.compare_digest(dk.hex(), hash_hex)
    except (ValueError, TypeError):
        return False


# ── tokens ──────────────────────────────────────────────────────────────── #
def _issue_token(conn, user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    conn.execute(
        "INSERT INTO auth_tokens (token, user_id, created_at) VALUES (?, ?, ?)",
        (token, user_id, now_iso()),
    )
    return token


def user_id_for_token(conn, token: str) -> str | None:
    row = conn.execute("SELECT user_id FROM auth_tokens WHERE token = ?", (token,)).fetchone()
    return row["user_id"] if row else None


# ── account ops ─────────────────────────────────────────────────────────── #
def _user_public(row) -> dict:
    return {"id": row["id"], "username": row["username"], "label": row["label"],
            "created_at": row["created_at"]}


def register(username: str, password: str) -> dict:
    username = username.strip()
    if len(username) < 3:
        raise HTTPException(status_code=400, detail="username must be at least 3 characters")
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="password must be at least 6 characters")
    with get_conn() as conn:
        exists = conn.execute(
            "SELECT 1 FROM users WHERE username = ? COLLATE NOCASE", (username,)
        ).fetchone()
        if exists:
            raise HTTPException(status_code=409, detail="username already taken")
        uid = new_id("user")
        conn.execute(
            "INSERT INTO users (id, username, password_hash, label, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (uid, username, hash_password(password), username, now_iso()),
        )
        token = _issue_token(conn, uid)
        row = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    return {"token": token, "user": _user_public(row)}


def login(username: str, password: str) -> dict:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username.strip(),)
        ).fetchone()
        if row is None or not verify_password(password, row["password_hash"]):
            raise HTTPException(status_code=401, detail="invalid username or password")
        token = _issue_token(conn, row["id"])
    return {"token": token, "user": _user_public(row)}


def logout(token: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM auth_tokens WHERE token = ?", (token,))


# ── FastAPI dependency ──────────────────────────────────────────────────── #
def current_user(authorization: str | None = Header(default=None)) -> dict:
    """Resolve the bearer token to the authenticated user, or 401."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    with get_conn() as conn:
        uid = user_id_for_token(conn, token)
        if uid is None:
            raise HTTPException(status_code=401, detail="invalid or expired token")
        row = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        if row is None:
            raise HTTPException(status_code=401, detail="user no longer exists")
    return _user_public(row)
