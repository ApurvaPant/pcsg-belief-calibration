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
import os
import re
import sqlite3
from typing import Any

# Ablation switch: set PCSG_DISABLE_QA_FIX=1 to revert the question–answer
# resolution, generalized slots and refinement-chain behaviour (fix steps 1-3),
# so the before/after can be measured on identical cases.
_QA_FIX = os.getenv("PCSG_DISABLE_QA_FIX") != "1"

from . import extraction, llm, ontology, salience
from .db import get_conn, now_iso
from .update import apply_assertion, apply_relations
from .utils import new_id

# P4 (no-fabrication): the assistant may only state facts present in the state
# block; anything missing is "I don't know" or a question — never a guess.
_CHAT_SYSTEM = (
    "You are a warm, attentive assistant who remembers things about the user.\n\n"
    "WHAT YOU KNOW ABOUT THE USER\n{state}\n\n"
    "How to use this:\n"
    "- Use what you know when it is relevant. Never mention a memory system or a graph.\n"
    "- Only state facts about the user that appear above. If a detail is not listed "
    "— including durations, names, dates, amounts, or whether something is true — say "
    "you don't know, or ask. NEVER guess or invent a detail, and never claim a "
    "relationship or preference that is not listed. Never invent specifics such as a "
    "car, phone number, email, address, brand, or date that is not listed above.\n"
    "- If the question ASSUMES something that is not listed (e.g. 'why did i quit my "
    "job?' when no job ending is recorded, or 'when did i move to X?' when no such move "
    "is recorded), or refers to a pet, vehicle, person or thing that is NOT listed "
    "above (e.g. asks about 'my cat' when only a dog is listed, or 'my car' when none "
    "is listed), do not play along or pretend it exists: say you have no record of it "
    "and, if helpful, state what you do know instead.\n"
    "- Items marked [ENDED] are no longer true; mention them only if the user asks "
    "about the past.\n"
    "- When the user states something durable and a useful detail is missing, you may "
    "ask ONE short follow-up — but never assert the detail yourself."
)

_HISTORY_LIMIT = 12
_CONTEXT_TURNS = 4
_SLOT_FLOOR = 0.60
_SLOT_EXPIRE_TURNS = 2  # fix step 2: a slot floors salience for 2 turns

# Assistant questions that open a slot for a specific attribute (PART 6).
_SLOT_QUESTIONS = [
    (re.compile(r"how long|since when|how many (?:years|months)|been together"), "duration"),
    (re.compile(r"how often|how frequently|how many times"), "frequency"),
    (re.compile(r"how (?:good|proficient|experienced)|what level"), "proficiency"),
]

# Questions that ask about the user's PAST (surface ended/superseded state).
_HISTORICAL = re.compile(
    r"\b(used to|use to|have i ever|did i ever|who was|who were|what did i|"
    r"where did i|did i use|were we|how long were|ever)\b", re.I)

# ── fix step 1: question–answer resolution (P8) ─────────────────────────────#
_QA_RESOLVE_SYSTEM = (
    "[[TASK:QA_RESOLVE]] Rewrite the user's FINAL reply as one standalone "
    "first-person sentence, using only information already in the recent "
    "conversation and the reply. Do not add any fact that is not present. "
    "If the reply does not clearly answer a single question, or which question "
    "it answers is ambiguous, respond with exactly: UNRESOLVED.\n"
    "Return only the sentence, or UNRESOLVED."
)
_IS_QUESTION = re.compile(
    r"\b(what|how|why|when|who|whom|whose|which|where|do|does|did|are|is|am|was|were|"
    r"can|could|would|will|should|have|has|tell me|any)\b", re.I)
_FIRST_PERSON_SV = re.compile(r"\b(i|i'?m|im|i'?ve|we|we'?re|my|mine)\b", re.I)
_INTEREST_VERB = re.compile(
    r"\b(love|loves|like|likes|enjoy|enjoys|into|follow|following|favou?rite|prefer|"
    r"prefers|passionate|interested in|fan of)\b", re.I)
_LEAD_ADVERB = re.compile(r"^\s*(mostly|usually|really|just|probably|maybe|mainly|kinda|a bit of|sort of)\s+", re.I)
# A bare negative / non-committal reply is not a refinement answer — it must not
# be attached as an attribute (and can never be destructive).
_NON_ANSWER = re.compile(
    r"^\s*(no|nope|nah|not really|not sure|dunno|idk|maybe not|they don'?t|he doesn'?t|"
    r"she doesn'?t|we don'?t|i don'?t|don'?t|doesn'?t|didn'?t|never)\b", re.I)


def _previous_assistant_question(history: list[dict[str, str]]) -> str | None:
    """The immediately-preceding assistant turn, iff it is a question."""
    for m in reversed(history):
        if m.get("role") == "assistant":
            c = (m.get("content") or "").strip()
            return c if (c.endswith("?") or bool(_IS_QUESTION.search(c))) else None
    return None


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


# A reply that forms a complete clause of its own — states a fact, names someone,
# or gives an imperative ("call me X") — is NOT a fragment answer, even without a
# first-person pronoun. Without this, a new fact stated right after the assistant
# asked a question ("actually, call me johnny now", "his name is rocky") gets
# swallowed by the slot/refine machinery instead of being extracted and applied.
_COMPLETE_CLAUSE = re.compile(
    r"\b(call me|name'?s|name is|i'?m\b|i am\b|"
    r"(?:my|his|her|its|their|our)\s+\w+\s+(?:is|are|was|were|name))\b", re.I)


def _is_fragment(text: str) -> bool:
    """A reply that does not stand alone: it lacks a first-person subject
    ("8 months", "reading books", "technical"). A reply that already carries its
    own subject or a complete clause ("i switched to rust", "call me johnny",
    "his name is rocky") is a full statement, not a fragment answer, even when
    short — so it is extracted normally rather than attached to a slot."""
    if _COMPLETE_CLAUSE.search(text):
        return False
    return not _FIRST_PERSON_SV.search(text)


def _expresses_interest(text: str) -> bool:
    return bool(_INTEREST_VERB.search(text))


def _resolve_qa(history: list[dict[str, str]], message: str) -> str:
    """P8 (item 4): rewrite a fragment reply into one standalone first-person
    sentence using the last few turns as context, or return 'UNRESOLVED' when
    ambiguous. On any error, fall back to the raw reply (never destructive)."""
    recent = history[-3:]
    ctx = "\n".join(f"{'USER' if m.get('role') == 'user' else 'ASSISTANT'}: {m.get('content', '')}"
                    for m in recent) or "(none)"
    try:
        res = llm.complete(
            messages=[{"role": "system", "content": _QA_RESOLVE_SYSTEM},
                      {"role": "user", "content":
                       f"Recent conversation (oldest first):\n{ctx}\n\nUser's reply: {message}"}],
            model=llm.resolve_model("classifier"), temperature=0.0, max_tokens=60)
        out = (res.get("text") or "").strip().strip('"').splitlines()
        return (out[0].strip() if out else "") or message
    except Exception:  # noqa: BLE001 — resolver down: fall back to the raw reply
        return message


def _attr_name_from_question(question: str) -> str:
    """Derive the qualifier name an OPEN-slot answer fills, from the question."""
    q = (question or "").lower()
    if "name" in q:                       # "what's your cat's name?" -> name
        return "name"
    if "how old" in q or "their age" in q or "your age" in q:
        return "age"
    if any(p in q for p in ("what kind of", "what type of", "what sort of", "what genre")):
        return "genre"
    if any(p in q for p in ("topic", "subject", "area", "specific")):
        return "topic"
    if "how often" in q:
        return "frequency"
    if "how long" in q or "since when" in q:
        return "duration"
    return "detail"


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
                    completion_tokens=0, latency_ms=0, salience_score=None, salient=False,
                    resolved_content=None) -> str:
    mid = new_id("msg")
    conn.execute(
        "INSERT INTO messages (id, session_id, role, content, resolved_content, created_at, "
        "prompt_tokens, completion_tokens, latency_ms, salience_score, salient) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (mid, session_id, role, content, resolved_content, now_iso(), prompt_tokens,
         completion_tokens, latency_ms, salience_score, 1 if salient else 0),
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
    lines += _edge_lines(conn, user_id)
    if not lines:
        return ""
    return "Known about the user so far:\n" + "\n".join(lines)


def _edge_lines(conn, user_id) -> list[str]:
    """Relations between the user's entities, so the reply can use them (a
    negated edge must read as 'do NOT get along', not be invisible)."""
    rows = conn.execute(
        "SELECT e.relation, e.polarity, s.value AS src, t.value AS dst "
        "FROM edges e JOIN nodes s ON s.id = e.source_node_id "
        "JOIN nodes t ON t.id = e.target_node_id "
        "WHERE e.user_id = ? AND s.status = 'active' AND t.status = 'active'",
        (user_id,),
    ).fetchall()
    out = []
    for r in rows:
        rel = r["relation"].replace("_", " ")
        neg = str(r["polarity"] or "positive").startswith("neg")
        if r["relation"] == "gets_along_with":
            phrase = "do NOT get along with" if neg else "gets along with"
        else:
            phrase = f"{'does NOT ' if neg else ''}{rel}"
        out.append(f"- {r['src']} {phrase} {r['dst']}")
    return out


# ── pending slots (PART 6) ──────────────────────────────────────────────── #
def _node_label(conn, node_id) -> str:
    n = conn.execute("SELECT dimension, key, value FROM nodes WHERE id = ?", (node_id,)).fetchone()
    return f"{n['dimension']}/{n['key']}={n['value']}" if n else ""


def _get_open_slot(conn, session_id):
    row = conn.execute(
        "SELECT * FROM pending_slots WHERE session_id = ? AND status = 'open' "
        "ORDER BY created_at DESC, rowid DESC LIMIT 1",  # rowid breaks timestamp ties
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


def _open_slot(conn, session_id, node_id, attribute_name, asked_at_message_id,
               slot_type="attribute", topic=None):
    # Store "" not NULL for open slots: legacy DBs have attribute_name NOT NULL.
    conn.execute(
        "INSERT INTO pending_slots (id, session_id, node_id, attribute_name, slot_type, "
        "topic, asked_at_message_id, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?)",
        (new_id("slot"), session_id, node_id, attribute_name or "", slot_type, topic,
         asked_at_message_id, now_iso()),
    )


def _detect_slot(reply, changes):
    """fix step 2: EVERY assistant question opens a slot. Returns a dict
    {node_id, attribute_name, slot_type, topic} or None.

    - attribute slot: a qualifier question (how long / how often / ...) about a
      node touched this turn.
    - open slot: any other question; node_id is the most recent node touched
      this turn (may be None), topic is the question itself.
    """
    low = reply.lower().strip()
    if not (low.endswith("?") or bool(_IS_QUESTION.search(low))):
        return None
    node_id = None
    for c in changes:  # the most recent node touched this turn
        if c.get("node_id") and c.get("action") in ("created", "reinforced", "superseded", "refined"):
            node_id = c["node_id"]
    for pat, attr in _SLOT_QUESTIONS:
        if pat.search(low) and node_id:
            return {"node_id": node_id, "attribute_name": attr,
                    "slot_type": "attribute", "topic": reply}
    if not _QA_FIX:  # ablation: only attribute slots existed before the fix
        return None
    return {"node_id": node_id, "attribute_name": None, "slot_type": "open", "topic": reply}


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
    failed = getattr(ext, "failed", False)
    conn.execute(
        "INSERT INTO extraction_log (id, message_id, context_window, frame_output, fill_output, "
        "model, prompt_version, accepted, reason_code, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (new_id("xlog"), message_id, json.dumps(context or []),
         json.dumps(getattr(ext, "frame_output", [])), json.dumps(getattr(ext, "fill_output", [])),
         ext.model, extraction.PROMPT_VERSION, 1 if accepted else 0,
         getattr(ext, "error", None) or reason_code, "failed" if failed else "ok", now_iso()),
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
    original_message = message

    # Stage 1: resolve session + read context, profile, open slot.
    with get_conn() as conn:
        sid = _resolve_session(conn, session_id, user_id, message)
        history = _history(conn, sid)
        open_slot = _get_open_slot(conn, sid)
        slot_type = (open_slot["slot_type"] or "attribute") if open_slot else None
        slot_hint = None
        if open_slot and slot_type == "attribute":  # only attribute slots use the hint
            slot_hint = {
                "attribute_name": open_slot["attribute_name"],
                "node_label": _node_label(conn, open_slot["node_id"]) if open_slot["node_id"] else "",
            }
        profile = _profile_summary(conn, user_id, historical=bool(_HISTORICAL.search(message)))

    # fix step 3: refinement chain. An OPEN slot bound to a recent node, answered
    # by a fragment that does NOT express a new interest, attaches the answer as
    # an attribute of THAT node rather than creating a new one.
    refine = None
    if (_QA_FIX and open_slot and open_slot["node_id"] and slot_type == "open"
            and _is_fragment(message) and not _expresses_interest(message)
            and not _NON_ANSWER.match(message.strip()) and _tokenize(message)):
        refine = {"node_id": open_slot["node_id"],
                  "attr": _attr_name_from_question(open_slot["topic"] or ""),
                  "value": ontology.normalize_value(message)}

    # fix step 1: Q–A resolution. Rewrite a fragment reply to the assistant's
    # question into a standalone first-person sentence BEFORE the gate. Skipped
    # for an attribute slot (the structured slot-hint path is more precise) and
    # when we are taking the refinement path above.
    resolved_message = message
    fragment_answer = False   # a fragment reply to a question (never destructive)
    if _QA_FIX and refine is None:
        prev_q = _previous_assistant_question(history)
        if prev_q and _is_fragment(message) and slot_type != "attribute":
            fragment_answer = True
            out = _resolve_qa(history, message)  # P8 sees the last turns (item 4)
            if out and out.strip().upper() != "UNRESOLVED":
                resolved_message = out
    rewritten = resolved_message.strip().lower() != message.strip().lower()
    work_text = resolved_message

    # Stage 2/3: gate + extraction (on the RESOLVED text; evidence keeps the
    # user's original words). The refinement path skips the model entirely.
    if refine is not None:
        is_query, extract_text = False, ""
        score, reason, classifier_model, raw_score, salient = _SLOT_FLOOR, "slot_refine", None, 0, True
        ext = extraction.ExtractionResult([], [], None, None)
    else:
        is_query, extract_text = salience.classify_message(work_text)
        if is_query:
            score, reason, classifier_model, raw_score, salient = 0.0, "retrieval_query", None, 0, False
        else:
            score, reason, classifier_model, raw_score = salience.score_salience(extract_text)
            if open_slot and score < _SLOT_FLOOR:  # fix step 2: slot floor of 60
                score, reason = _SLOT_FLOOR, reason + "; slot_floor"
            salient = salience.is_salient(score)
        if rewritten:
            reason += "; qa_resolved"
        elif fragment_answer:
            reason += "; qa_unresolved"
        if salient and salience.is_quarantined(original_message):
            # Prompt-injection / meta-instruction / forwarded content: reply, but
            # never extract facts from it into the user's graph (safety). Checked
            # on the ORIGINAL message so a trailing question can't strip the marker.
            reason += "; quarantined_injection"
            ext = extraction.ExtractionResult([], [], None, None)
        elif salient:
            end_forced, end_object = salience.detect_end(extract_text)
            ext = extraction.extract_assertions(extract_text, context=history, pending_slot=slot_hint,
                                                force_end=end_forced, end_object=end_object)
            if rewritten:  # evidence quote stays the user's ORIGINAL words
                for a in ext.kept:
                    a["quote"] = original_message
        else:
            ext = extraction.ExtractionResult([], [], None, None)

    # Stage 4: assistant reply (always generated from the original message).
    system = _CHAT_SYSTEM.format(state=profile or "(nothing recorded about the user yet)")
    chat_messages = [{"role": "system", "content": system}, *history[-_CONTEXT_TURNS:],
                     {"role": "user", "content": original_message}]
    # §6: the chat reply must still be returned even if the model call fails
    # (after retries). On failure, degrade gracefully instead of 500-ing — the
    # write path above has already run and is unaffected.
    try:
        reply_result = llm.complete(messages=chat_messages, model=llm.resolve_model("chat"))
        reply = reply_result["text"] or "Got it."
    except Exception:  # noqa: BLE001
        reply_result = {"prompt_tokens": 0, "completion_tokens": 0, "latency_ms": 0}
        reply = "Sorry — I'm having trouble responding right now, but I've noted what you said."

    # Stage 5: persist + reconcile.
    changes: list[dict[str, Any]] = []
    relations_made: list[dict[str, Any]] = []
    with get_conn() as conn:
        user_msg_id = _insert_message(conn, sid, "user", original_message,
                                      salience_score=score, salient=salient,
                                      resolved_content=resolved_message if rewritten else None)
        _log_gate(conn, sid, user_msg_id, salient, score, raw_score, reason, classifier_model,
                  frame_tokens=ext.frame_tokens, fill_tokens=ext.fill_tokens)

        if refine is not None:
            tnode = conn.execute("SELECT dimension, key, value FROM nodes WHERE id = ?",
                                 (refine["node_id"],)).fetchone()
            if tnode and refine["value"]:
                ref_assertion = {
                    "dimension": tnode["dimension"], "key": tnode["key"], "value": tnode["value"],
                    "operation": "REFINE", "subject": "self", "replaces_value": None,
                    "attributes": [{"name": refine["attr"], "value": refine["value"], "unit": None}],
                    "quote": original_message, "confidence": 0.8, "explicitness": "explicit",
                    "target_node": {"dimension": tnode["dimension"], "key": tnode["key"],
                                    "value": tnode["value"]},
                }
                changes.append(apply_assertion(conn, user_id, sid, user_msg_id, ref_assertion,
                                               model=llm.resolve_model("extractor")))
        else:
            for assertion in ext.kept:
                changes.append(apply_assertion(conn, user_id, sid, user_msg_id, assertion,
                                               model=ext.model, qa_resolved=fragment_answer))
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

            # PART 8: relations (edges only when explicitly stated + endpoints exist).
            relations_made, relations_rejected = apply_relations(conn, user_id, ext.relations, work_text)
            for r in relations_made:
                changes.append({
                    "action": "linked", "node_id": None, "dimension": "", "key": r["relation"],
                    "value": f"{r['from']} -> {r['to']}", "previous_node_id": None,
                    "confidence_calibrated": None, "reason_code": None,
                })
            for r in relations_rejected:
                changes.append({
                    "action": "ignored", "node_id": None, "dimension": "edge", "key": r["relation"],
                    "value": f"{r['from']} -> {r['to']}", "previous_node_id": None,
                    "confidence_calibrated": None, "reason_code": r["reason"],
                })

        # PART 6: fill the open slot if this turn refined/reinforced its node.
        if open_slot and (refine is not None or any(
                c.get("action") in ("refined", "reinforced") and c.get("node_id") == open_slot["node_id"]
                for c in changes)):
            conn.execute("UPDATE pending_slots SET status = 'filled' WHERE id = ?", (open_slot["id"],))

        for ch in changes:
            _log_event(conn, sid, user_msg_id, ch, ext.raw)
        if salient and not changes:
            _log_event(conn, sid, user_msg_id,
                       {"action": "none", "dimension": "", "key": "", "value": "",
                        "node_id": None, "reason_code": None}, ext.raw)

        # PART 9: log every model extraction attempt (for future distillation).
        if salient and refine is None:
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
        # fix step 2: open a slot for ANY question the assistant just asked.
        det = _detect_slot(reply, changes)
        if det:
            _open_slot(conn, sid, det["node_id"], det["attribute_name"], assistant_msg_id,
                       det["slot_type"], det["topic"])

    return {
        "session_id": sid, "user_id": user_id, "reply": reply, "salient": salient,
        "salience_score": round(score, 3), "changes": changes,
        "latency_ms": reply_result.get("latency_ms", 0),
    }
