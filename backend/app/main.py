"""FastAPI application: auth, per-user routes, CORS, startup."""
from __future__ import annotations

import os

from fastapi import Depends, FastAPI, Header, HTTPException

from fastapi.middleware.cors import CORSMiddleware

from . import auth
from . import graph as graph_mod
from . import llm
from . import pipeline
from .config import settings
from .db import get_conn, init_db, now_iso
from .models import (
    AuthRequest,
    AuthResponse,
    ChatRequest,
    ChatResponse,
    CreateSessionRequest,
    MergeRequest,
    RenameSessionRequest,
    SessionOut,
    UserOut,
)

app = FastAPI(title="User-State Graph Chat", version="3.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.server.cors_origins,
    # Dev convenience: also accept any other origin (localhost, a LAN IP, or a
    # forwarded host) so the browser is never CORS-blocked. The regex makes the
    # middleware echo the specific Origin, so allow_credentials stays valid.
    allow_origin_regex=r".*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Test-mode debug endpoints (§1.1): mounted only under PCSG_TEST_MODE=1 so they
# can never be reached in a normal run. They add no production behaviour.
if os.getenv("PCSG_TEST_MODE") == "1":
    from . import debug_routes
    app.include_router(debug_routes.router)


@app.on_event("startup")
def _startup() -> None:
    init_db()
    # Fail loudly if a real provider is configured but unusable (missing key /
    # unreachable / 401) — never silently fall back to mock.
    llm.startup_check()


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "provider": settings.llm.provider}


# ── Auth ────────────────────────────────────────────────────────────────── #
@app.post("/auth/register", response_model=AuthResponse)
def register(req: AuthRequest) -> AuthResponse:
    return AuthResponse(**auth.register(req.username, req.password))


@app.post("/auth/login", response_model=AuthResponse)
def login(req: AuthRequest) -> AuthResponse:
    return AuthResponse(**auth.login(req.username, req.password))


@app.post("/auth/logout")
def logout(authorization: str | None = Header(default=None)) -> dict:
    if authorization and authorization.lower().startswith("bearer "):
        auth.logout(authorization.split(" ", 1)[1].strip())
    return {"ok": True}


@app.get("/auth/me", response_model=UserOut)
def me(user: dict = Depends(auth.current_user)) -> UserOut:
    return UserOut(**user)


# ── Sessions (current user) ─────────────────────────────────────────────── #
@app.get("/me/sessions", response_model=list[SessionOut])
def list_sessions(user: dict = Depends(auth.current_user)) -> list[SessionOut]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, user_id, title, created_at, closed_at FROM sessions "
            "WHERE user_id = ? ORDER BY created_at DESC",
            (user["id"],),
        ).fetchall()
    return [SessionOut(**dict(r)) for r in rows]


@app.post("/me/sessions", response_model=SessionOut)
def new_session(req: CreateSessionRequest, user: dict = Depends(auth.current_user)) -> SessionOut:
    with get_conn() as conn:
        sid = pipeline.create_session(conn, user["id"], req.title)
        row = conn.execute(
            "SELECT id, user_id, title, created_at, closed_at FROM sessions WHERE id = ?",
            (sid,),
        ).fetchone()
    return SessionOut(**dict(row))


@app.patch("/sessions/{session_id}", response_model=SessionOut)
def rename_session(
    session_id: str,
    req: RenameSessionRequest,
    user: dict = Depends(auth.current_user),
) -> SessionOut:
    """Rename a chat (the kebab-menu 'Rename' action)."""
    with get_conn() as conn:
        _owned_session(conn, session_id, user["id"])
        conn.execute("UPDATE sessions SET title = ? WHERE id = ?", (req.title.strip(), session_id))
        row = conn.execute(
            "SELECT id, user_id, title, created_at, closed_at FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
    return SessionOut(**dict(row))


@app.get("/me/graph")
def user_graph(user: dict = Depends(auth.current_user)) -> dict:
    return graph_mod.build_graph(user["id"])


@app.get("/me/memory")
def user_memory(user: dict = Depends(auth.current_user)) -> dict:
    """Everything the system models about the user, grouped-ready for the
    Memory page, with attributes + a representative quote per fact."""
    return graph_mod.build_memory(user["id"])


def _owned_session(conn, session_id: str, user_id: str):
    row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if row is None or row["user_id"] != user_id:
        raise HTTPException(status_code=404, detail="session not found")
    return row


@app.get("/sessions/{session_id}/messages")
def session_messages(session_id: str, user: dict = Depends(auth.current_user)) -> list[dict]:
    with get_conn() as conn:
        _owned_session(conn, session_id, user["id"])
        rows = conn.execute(
            "SELECT id, role, content, created_at, salience_score, salient "
            "FROM messages WHERE session_id = ? ORDER BY created_at ASC",
            (session_id,),
        ).fetchall()
    return [
        {
            "id": r["id"], "role": r["role"], "content": r["content"],
            "created_at": r["created_at"], "salience_score": r["salience_score"],
            "salient": bool(r["salient"]),
        }
        for r in rows
    ]


@app.delete("/sessions/{session_id}")
def delete_session(
    session_id: str,
    purge_memory: bool = False,
    user: dict = Depends(auth.current_user),
) -> dict:
    """Delete a chat. With purge_memory=true, also remove the graph nodes/
    attributes that were learned ONLY from this chat (nodes also grounded by
    other chats are kept). Without it, the learned state is preserved."""
    from . import update
    with get_conn() as conn:
        _owned_session(conn, session_id, user["id"])
        return update.delete_session(conn, user["id"], session_id, purge_memory)


@app.post("/sessions/{session_id}/close")
def close_session(session_id: str, user: dict = Depends(auth.current_user)) -> dict:
    """Session-end consolidation hook. Stubbed — future cross-session merging."""
    with get_conn() as conn:
        _owned_session(conn, session_id, user["id"])
        conn.execute("UPDATE sessions SET closed_at = ? WHERE id = ?", (now_iso(), session_id))
    return {"session_id": session_id, "consolidated": False}


# ── Chat + nodes ────────────────────────────────────────────────────────── #
@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest, user: dict = Depends(auth.current_user)) -> ChatResponse:
    if req.session_id:
        with get_conn() as conn:
            _owned_session(conn, req.session_id, user["id"])
    result = pipeline.process_turn(req.message, req.session_id, user["id"])
    return ChatResponse(**result)


@app.get("/messages/{message_id}/outcome")
def message_outcome(message_id: str, user: dict = Depends(auth.current_user)) -> dict:
    """Per-assertion audit for one message: what was extracted, what the
    resolver matched, the similarity, the final action, and the reason_code —
    so 'why didn't this message change the graph?' is answerable without logs."""
    with get_conn() as conn:
        owner = conn.execute(
            "SELECT s.user_id AS uid FROM messages m JOIN sessions s ON s.id = m.session_id "
            "WHERE m.id = ?", (message_id,),
        ).fetchone()
        if owner is None or owner["uid"] != user["id"]:
            raise HTTPException(status_code=404, detail="message not found")
        gate = conn.execute(
            "SELECT salient, score, raw_score, reason FROM gate_log WHERE message_id = ?",
            (message_id,),
        ).fetchone()
        rows = conn.execute(
            "SELECT action, dimension, key, value, node_id, matched_label, similarity, "
            "reason_code, extractor_json FROM events WHERE message_id = ? ORDER BY created_at",
            (message_id,),
        ).fetchall()
    return {
        "message_id": message_id,
        "gate": dict(gate) if gate else None,
        "assertions": [
            {
                "action": r["action"],
                "extracted": f"{r['dimension']}/{r['key']}={r['value']}" if r["key"] else r["dimension"],
                "matched_node_id": r["node_id"],
                "matched_label": r["matched_label"],
                "similarity": r["similarity"],
                "reason_code": r["reason_code"],
            }
            for r in rows
        ],
        "extractor_json": rows[0]["extractor_json"] if rows else None,
    }


@app.get("/node/{node_id}")
def node(node_id: str, user: dict = Depends(auth.current_user)) -> dict:
    detail = graph_mod.get_node_detail(node_id)
    if detail is None or detail.get("user_id") != user["id"]:
        raise HTTPException(status_code=404, detail="node not found")
    return detail


@app.delete("/nodes/{node_id}")
def forget_node(node_id: str, user: dict = Depends(auth.current_user)) -> dict:
    """Erase a fact the user asked to forget, with its history/attributes/
    evidence/relations. Explicit and irreversible (unlike END/supersede)."""
    from . import update
    with get_conn() as conn:
        n = conn.execute("SELECT user_id FROM nodes WHERE id = ?", (node_id,)).fetchone()
        if n is None or n["user_id"] != user["id"]:
            raise HTTPException(status_code=404, detail="node not found")
        res = update.forget_node(conn, user["id"], node_id)
    if not res.get("forgotten"):
        raise HTTPException(status_code=400, detail=res.get("reason", "forget failed"))
    return res


@app.post("/nodes/{node_id}/restore")
def restore_node(node_id: str, user: dict = Depends(auth.current_user)) -> dict:
    """Undo an END/supersede: reactivate an ended or superseded node (item 6)."""
    from . import update
    with get_conn() as conn:
        n = conn.execute("SELECT user_id FROM nodes WHERE id = ?", (node_id,)).fetchone()
        if n is None or n["user_id"] != user["id"]:
            raise HTTPException(status_code=404, detail="node not found")
        res = update.restore_node(conn, user["id"], node_id)
    if not res.get("restored"):
        raise HTTPException(status_code=400, detail=res.get("reason", "restore failed"))
    return res


@app.post("/events/{event_id}/revert")
def revert_event(event_id: str, user: dict = Depends(auth.current_user)) -> dict:
    """Restore the node a given event acted on, to its pre-event state (item 6)."""
    from . import update
    with get_conn() as conn:
        res = update.revert_event(conn, user["id"], event_id)
    if not res.get("restored"):
        raise HTTPException(status_code=400, detail=res.get("reason", "revert failed"))
    return res


@app.post("/me/nodes/merge")
def merge_nodes(req: MergeRequest, user: dict = Depends(auth.current_user)) -> dict:
    """Collapse a duplicate node into another (bug 3 cleanup)."""
    from . import update
    with get_conn() as conn:
        ok = update.merge_nodes(conn, user["id"], req.keep_id, req.dup_id)
    if not ok:
        raise HTTPException(status_code=400, detail="merge failed (check ids / ownership)")
    return {"merged": True, "keep_id": req.keep_id, "source": req.dup_id}


@app.post("/nodes/{node_id}/merge_into/{target_id}")
def merge_into(node_id: str, target_id: str, user: dict = Depends(auth.current_user)) -> dict:
    """Merge source node `node_id` INTO `target_id`: move evidence/attributes/
    edges across and mark the source merged (PART 4)."""
    from . import update
    with get_conn() as conn:
        ok = update.merge_nodes(conn, user["id"], keep_id=target_id, dup_id=node_id)
    if not ok:
        raise HTTPException(status_code=400, detail="merge failed (check ids / ownership)")
    return {"merged": True, "target_id": target_id, "source": node_id}
