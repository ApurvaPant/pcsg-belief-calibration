"""Turn orchestration: message in -> reply + graph mutations out.

Memory is user-scoped. A turn belongs to a session; the session belongs to a
user; assertions reconcile into that user's single graph, so state carries
across all of the user's sessions.

Flow per user turn:
    1. resolve session, read history + profile + any open pending slot
    2. score salience (rules + classifier), with a floor when a slot is open
    3. if salient: extract assertions (with conversational context + slot hint)
    4. generate the assistant reply (personalized with the user's profile)
    5. persist + reconcile: nodes, attributes, relations, events, slot updates
"""
from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from . import extraction, llm, salience
from .db import get_conn, now_iso
from .update import apply_assertion, apply_relations
from .utils import new_id

_CHAT_SYSTEM = (
    "You are a warm, attentive assistant that gradually builds an understanding "
    "of the user. Keep replies concise and natural. When the user states a fact "
    "with a detail worth pinning down (how long, how often, since when), ask one "
    "short follow-up. Do not mention that you are extracting facts or a graph."
)

_HISTORY_LIMIT = 12
_CONTEXT_TURNS = 4
_SLOT_FLOOR = 0.60
_SLOT_EXPIRE_TURNS = 3

# Assistant questions that open a slot for a specific attribute (PART 6).
_SLOT_QUESTIONS = [
    (re.compile(r"how long|since when|how many (?:years|months)|been together"), "duration"),
    (re.compile(r"how often|how frequently|how many times"), "frequency"),
    (re.compile(r"how (?:good|proficient|experienced)|what level"), "proficiency"),
]

# Questions that ask about the user's PAST (surface ended/superseded state).
_HISTORICAL = re.compile(
    r"\b(used to|have i ever|did i ever|who was|who were|what did i|were we|how long were)\b", re.I)


# ── sessions & messages ─────────────────────────────────────────────────── #
def _title_from(message: str) -> str:
    return (message[:48] + "…") if len(message) > 48 else message


def create_session(conn: sqlite3.Connection, user_id: str, title: str | None = None) -> str:
    sid = new_id("sess")
    conn.execute(
        "INSERT INTO sessions (id, user_id, title, created_at) VALUES (?, ?, ?, ?)",
        (sid, user_id, title or "New chat", now_iso()),
    )
    return sid


def _resolve_session(conn, session_id, user_id, first_message) -> str:
    if session_id:
        row = conn.execute("SELECT id, user_id FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row and row["user_id"] == user_id:
            conn.execute(
                "UPDATE sessions SET title = ? WHERE id = ? AND (title IS NULL OR title = 'New chat')",
                (_title_from(first_message), session_id),
            )
            return row["id"]
    return create_session(conn, user_id, _title_from(first_message))


def _history(conn, session_id, limit=_HISTORY_LIMIT):
    rows = conn.execute(
        "SELECT role, content FROM messages WHERE session_id = ? ORDER BY created_at ASC LIMIT ?",
        (session_id, limit),
    ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in rows]


def _insert_message(conn, session_id, role, content, *, prompt_tokens=0,
                    completion_tokens=0, latency_ms=0, salience_score=None, salient=False) -> str:
    mid = new_id("msg")
    conn.execute(
        "INSERT INTO messages (id, session_id, role, content, created_at, prompt_tokens, "
        "completion_tokens, latency_ms, salience_score, salient) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (mid, session_id, role, content, now_iso(), prompt_tokens, completion_tokens,
         latency_ms, salience_score, 1 if salient else 0),
    )
    return mid


# ── retrieval (PART 7) ──────────────────────────────────────────────────── #
def _fmt_node(conn, n) -> str:
    attrs = conn.execute(
        "SELECT name, value, unit FROM attributes WHERE node_id = ? AND status = 'active'",
        (n["id"],),
    ).fetchall()
    a = ", ".join(f"{x['name']}: {x['value']}{' ' + x['unit'] if x['unit'] else ''}" for x in attrs)
    astr = f"  ({a}, conf {n['confidence_calibrated']})" if a else f"  (conf {n['confidence_calibrated']})"
    mark = ""
    if n["status"] != "active":
        mark = f" [{(n['status_detail'] or 'superseded').upper()}]"
    return f"- {n['dimension']}.{n['key']} = {n['value']}{astr}{mark}"


def _profile_summary(conn, user_id, historical=False) -> str:
    active = conn.execute(
        "SELECT * FROM nodes WHERE user_id = ? AND status = 'active' ORDER BY dimension",
        (user_id,),
    ).fetchall()
    lines = [_fmt_node(conn, n) for n in active]
    if historical:
        old = conn.execute(
            "SELECT * FROM nodes WHERE user_id = ? AND status != 'active' ORDER BY created_at",
            (user_id,),
        ).fetchall()
        lines += [_fmt_node(conn, n) for n in old]
    if not lines:
        return ""
    return "Known about the user so far:\n" + "\n".join(lines)


# ── pending slots (PART 6) ──────────────────────────────────────────────── #
def _node_label(conn, node_id) -> str:
    n = conn.execute("SELECT dimension, key, value FROM nodes WHERE id = ?", (node_id,)).fetchone()
    return f"{n['dimension']}/{n['key']}={n['value']}" if n else ""


def _get_open_slot(conn, session_id):
    row = conn.execute(
        "SELECT * FROM pending_slots WHERE session_id = ? AND status = 'open' "
        "ORDER BY created_at DESC LIMIT 1",
        (session_id,),
    ).fetchone()
    if not row:
        return None
    if row["asked_at_message_id"]:
        newer = conn.execute(
            "SELECT COUNT(*) c FROM messages WHERE session_id = ? AND role = 'user' "
            "AND created_at > (SELECT created_at FROM messages WHERE id = ?)",
            (session_id, row["asked_at_message_id"]),
        ).fetchone()["c"]
        if newer >= _SLOT_EXPIRE_TURNS:
            conn.execute("UPDATE pending_slots SET status = 'expired' WHERE id = ?", (row["id"],))
            return None
    return row


def _open_slot(conn, session_id, node_id, attribute_name, asked_at_message_id):
    conn.execute(
        "INSERT INTO pending_slots (id, session_id, node_id, attribute_name, "
        "asked_at_message_id, status, created_at) VALUES (?, ?, ?, ?, ?, 'open', ?)",
        (new_id("slot"), session_id, node_id, attribute_name, asked_at_message_id, now_iso()),
    )


def _detect_slot(reply, changes):
    """Did the reply ask about an attribute of a node created this turn?"""
    low = reply.lower()
    for pat, attr in _SLOT_QUESTIONS:
        if pat.search(low):
            for c in changes:
                if c.get("node_id") and c.get("action") in ("created", "reinforced", "superseded", "refined"):
                    return c["node_id"], attr
    return None


def _log_gate(conn, session_id, message_id, salient, score, raw_score, reason,
              classifier_model, frame_tokens=0, fill_tokens=0):
    conn.execute(
        "INSERT INTO gate_log (id, session_id, message_id, salient, score, raw_score, reason, "
        "classifier_model, frame_tokens, fill_tokens, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (new_id("gl"), session_id, message_id, 1 if salient else 0, score, raw_score, reason,
         classifier_model, frame_tokens, fill_tokens, now_iso()),
    )


def _log_extraction(conn, message_id, context, ext, accepted, reason_code):
    conn.execute(
        "INSERT INTO extraction_log (id, message_id, context_window, frame_output, fill_output, "
        "model, prompt_version, accepted, reason_code, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (new_id("xlog"), message_id, json.dumps(context or []),
         json.dumps(getattr(ext, "frame_output", [])), json.dumps(getattr(ext, "fill_output", [])),
         ext.model, extraction.PROMPT_VERSION, 1 if accepted else 0, reason_code, now_iso()),
    )


def _log_event(conn, session_id, message_id, change, extractor_json):
    conn.execute(
        "INSERT INTO events (id, session_id, message_id, action, dimension, key, value, "
        "node_id, matched_label, similarity, reason_code, extractor_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (new_id("evt"), session_id, message_id, change.get("action"), change.get("dimension"),
         change.get("key"), change.get("value"), change.get("node_id"), change.get("matched_label"),
         change.get("similarity"), change.get("reason_code"), extractor_json, now_iso()),
    )


# ── the turn ────────────────────────────────────────────────────────────── #
def process_turn(message: str, session_id: str | None, user_id: str) -> dict[str, Any]:
    # Stage 1: resolve session + read context, profile, open slot.
    with get_conn() as conn:
        sid = _resolve_session(conn, session_id, user_id, message)
        history = _history(conn, sid)
        open_slot = _get_open_slot(conn, sid)
        slot_hint = None
        if open_slot:
            slot_hint = {
                "attribute_name": open_slot["attribute_name"],
                "node_label": _node_label(conn, open_slot["node_id"]),
            }
        profile = _profile_summary(conn, user_id, historical=bool(_HISTORICAL.search(message)))

    # Bug 1: a pure interrogative is a retrieval query — never extract from it.
    is_query, extract_text = salience.classify_message(message)

    # Stage 2: salience (slot floor when we just asked a question).
    if is_query:
        score, reason, classifier_model, raw_score, salient = 0.0, "retrieval_query", None, 0, False
    else:
        score, reason, classifier_model, raw_score = salience.score_salience(extract_text)
        if open_slot and score < _SLOT_FLOOR:
            score, reason = _SLOT_FLOOR, reason + "; slot_floor"
        salient = salience.is_salient(score)

    # Stage 3: extraction (only the declarative half), with context + slot hint.
    # Tier-0 rule-matched END is a constraint the extractor cannot override.
    if salient:
        end_forced, end_object = salience.detect_end(extract_text)
        ext = extraction.extract_assertions(extract_text, context=history, pending_slot=slot_hint,
                                            force_end=end_forced, end_object=end_object)
    else:
        ext = extraction.ExtractionResult([], [], None, None)

    # Stage 4: assistant reply.
    system = _CHAT_SYSTEM + (f"\n\n{profile}" if profile else "")
    chat_messages = [{"role": "system", "content": system}, *history[-_CONTEXT_TURNS:],
                     {"role": "user", "content": message}]
    reply_result = llm.complete(messages=chat_messages, model=llm.resolve_model("chat"))
    reply = reply_result["text"]

    # Stage 5: persist + reconcile.
    changes: list[dict[str, Any]] = []
    relations_made: list[dict[str, Any]] = []
    with get_conn() as conn:
        user_msg_id = _insert_message(conn, sid, "user", message,
                                      salience_score=score, salient=salient)
        _log_gate(conn, sid, user_msg_id, salient, score, raw_score, reason, classifier_model,
                  frame_tokens=ext.frame_tokens, fill_tokens=ext.fill_tokens)

        for assertion in ext.kept:
            changes.append(apply_assertion(conn, user_id, sid, user_msg_id, assertion, model=ext.model))
        for raw_item, rcode in ext.dropped:
            item = raw_item if isinstance(raw_item, dict) else {}
            changes.append({
                "action": "ignored", "node_id": None,
                "dimension": str(item.get("dimension", "") or ""),
                "key": str(item.get("key", "") or ""),
                "value": str(item.get("value", "") or ""),
                "previous_node_id": None, "confidence_calibrated": None, "reason_code": rcode,
            })

        # Bug 1: surface the read path so a query is visibly RETRIEVAL_QUERY.
        if is_query:
            changes.append({
                "action": "retrieval_query", "node_id": None, "dimension": "", "key": "",
                "value": "", "previous_node_id": None, "confidence_calibrated": None,
                "reason_code": None,
            })

        # PART 8: relations (edges only when explicitly stated + both endpoints exist).
        relations_made, relations_rejected = apply_relations(conn, user_id, ext.relations, message)
        for r in relations_made:
            changes.append({
                "action": "linked", "node_id": None, "dimension": "", "key": r["relation"],
                "value": f"{r['from']} → {r['to']}", "previous_node_id": None,
                "confidence_calibrated": None, "reason_code": None,
            })
        for r in relations_rejected:
            changes.append({
                "action": "ignored", "node_id": None, "dimension": "edge", "key": r["relation"],
                "value": f"{r['from']} → {r['to']}", "previous_node_id": None,
                "confidence_calibrated": None, "reason_code": r["reason"],
            })

        # PART 6: fill the open slot if this turn refined its node.
        if open_slot and any(c.get("action") == "refined" and c.get("node_id") == open_slot["node_id"]
                             for c in changes):
            conn.execute("UPDATE pending_slots SET status = 'filled' WHERE id = ?", (open_slot["id"],))

        for ch in changes:
            _log_event(conn, sid, user_msg_id, ch, ext.raw)
        if salient and not changes:
            _log_event(conn, sid, user_msg_id,
                       {"action": "none", "dimension": "", "key": "", "value": "",
                        "node_id": None, "reason_code": None}, ext.raw)

        # PART 9: log every extraction attempt (for future distillation).
        if salient:
            accepted = any(c.get("action") in ("created", "reinforced", "superseded", "ended", "refined", "linked")
                           for c in changes)
            rc = next((c.get("reason_code") for c in changes if c.get("reason_code")), None)
            _log_extraction(conn, user_msg_id, history, ext, accepted, rc)

        assistant_msg_id = _insert_message(
            conn, sid, "assistant", reply,
            prompt_tokens=reply_result.get("prompt_tokens", 0),
            completion_tokens=reply_result.get("completion_tokens", 0),
            latency_ms=reply_result.get("latency_ms", 0),
        )
        # PART 6: open a new slot if the assistant just asked about an attribute.
        det = _detect_slot(reply, changes)
        if det:
            node_id, attr = det
            _open_slot(conn, sid, node_id, attr, assistant_msg_id)

    return {
        "session_id": sid, "user_id": user_id, "reply": reply, "salient": salient,
        "salience_score": round(score, 3), "changes": changes,
        "latency_ms": reply_result.get("latency_ms", 0),
    }
