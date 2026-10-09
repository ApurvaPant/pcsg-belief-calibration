"""Update policy — applies extraction operations to the temporal graph.

Reconciliation is driven by slot **identity** and **cardinality**:

    SINGLE slot identity : (scope, dimension, key)
    MULTI  slot identity : (scope, dimension, key, value_normalized)

`scope` is the graph owner. Today that is the conversation id; Part 5 swaps it
for a user id so state carries across sessions — the signature already takes a
`scope_id` so that change is local.

Operations:
    ADD/UPDATE, identity exists            -> reinforce (Noisy-OR), add evidence
    ADD, SINGLE, identity exists, new value-> supersede old, create linked new
    ADD, MULTI, new value                  -> create new node (siblings untouched)
    END                                    -> close the matching node only
    replaces_value set                     -> end the named node, add linked new
"""
from __future__ import annotations

import difflib
import re
import sqlite3
from typing import Any

from . import ontology, salience
from .config import settings
from .db import now_iso
from .ontology import (
    DETAIL_ENDED,
    STATUS_ACTIVE,
    STATUS_SUPERSEDED,
)
from .utils import new_id

# An explicit replacement/swap marker — the only phrasing that may supersede.
_REPLACEMENT_RE = re.compile(
    r"\b(switched|swapped|replaced|changed|moved)\b|\binstead of\b|\bnow\b|"
    r"\bthese days\b|\bnowadays\b", re.I)


# ── Destructive-operation guard (critical: never end a correct node on weak
# evidence such as a plain negation, a rewritten fragment, or low confidence) ─#
def _names_target(quote: str, node) -> bool:
    """The quote names the node it would close (its value or key tokens)."""
    q = (quote or "").lower()
    toks = [t for t in ontology.normalize_value(node["value"]).split() if len(t) > 2]
    toks += [t for t in node["key"].split("_") if len(t) > 2]
    return any(t in q for t in toks) if toks else False


def _destructive_ok(kind: str, node, assertion: dict, qa_resolved: bool) -> tuple[bool, str | None]:
    """May this END/supersede be applied? Returns (ok, reason_if_blocked)."""
    quote = assertion.get("quote", "") or ""
    if qa_resolved:  # a Q-A-rewritten fragment must never destroy state
        return False, "fragment_rewrite"
    try:
        conf = float(assertion.get("confidence", 0.0))
    except (TypeError, ValueError):
        conf = 0.0
    if conf < settings.pipeline.destructive_min_conf:
        return False, "low_confidence"
    if kind == "end":
        if not salience.has_cessation(quote):
            return False, "no_cessation_verb"   # plain negation is not an END
        if not _names_target(quote, node):
            return False, "target_not_named"
        return True, None
    # supersede: a new single-valued fact may replace the old one, but never from
    # a rewritten fragment or at low confidence (conf/fragment checked above). A
    # strict "explicit replacement word" requirement would wrongly block ordinary
    # corrections ("my name is X"), so it is not required here — the real
    # negation-driven risk manifests as END, which is guarded strictly above.
    return True, None


def _queue_confirmation(conn, user_id, session_id, message_id, kind, node, assertion, reason) -> None:
    conn.execute(
        "INSERT INTO pending_confirmation (id, user_id, session_id, message_id, kind, node_id, "
        "dimension, key, value, quote, reason_code, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)",
        (new_id("pc"), user_id, session_id, message_id, kind, node["id"] if node else None,
         assertion.get("dimension"), assertion.get("key"), assertion.get("value"),
         assertion.get("quote", ""), reason, now_iso()),
    )


# ── confidence maths ────────────────────────────────────────────────────── #
def calibrate(raw: float) -> float:
    """Shrink a raw confidence toward a prior to avoid overconfidence."""
    prior = settings.pipeline.calibration_prior
    shrink = settings.pipeline.calibration_shrink
    calibrated = prior + shrink * (raw - prior)
    return round(max(0.0, min(1.0, calibrated)), 4)


def noisy_or(a: float, b: float) -> float:
    """Combine two independent evidence confidences: 1 - (1-a)(1-b)."""
    return round(1.0 - (1.0 - a) * (1.0 - b), 4)


# ── low-level helpers ───────────────────────────────────────────────────── #
def _add_evidence(conn, node_id, session_id, message_id, quote) -> None:
    conn.execute(
        "INSERT INTO evidence (id, node_id, session_id, message_id, quote, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (new_id("ev"), node_id, session_id, message_id, quote, now_iso()),
    )


def _add_attr_evidence(conn, attribute_id, session_id, message_id, quote) -> None:
    conn.execute(
        "INSERT INTO evidence (id, attribute_id, session_id, message_id, quote, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (new_id("ev"), attribute_id, session_id, message_id, quote, now_iso()),
    )


def _active_attribute(conn, node_id, name):
    return conn.execute(
        "SELECT * FROM attributes WHERE node_id = ? AND name = ? AND status = 'active' "
        "ORDER BY created_at DESC LIMIT 1",
        (node_id, name),
    ).fetchone()


def apply_attribute(conn, node_id, dimension, attr, confidence, session_id, message_id, quote) -> None:
    """Write one qualifier onto a node. A new value for (node_id, name)
    supersedes the old one (SINGLE-style) and links via previous_attribute_id.
    confidence_raw is never modified after insert."""
    name = ontology.normalize_key(attr.get("name"))
    value = str(attr.get("value", "")).strip()
    if not name or not value or not ontology.allowed_attribute(dimension, name):
        return
    unit = attr.get("unit")
    ts = now_iso()
    existing = _active_attribute(conn, node_id, name)
    if existing is not None and ontology.normalize_value(str(existing["value"])) == ontology.normalize_value(value) and (existing["unit"] or None) == (unit or None):
        return  # identical — nothing to do (attribute confidence is immutable)
    prev_id = None
    if existing is not None:
        conn.execute(
            "UPDATE attributes SET status = 'superseded', valid_to = ?, updated_at = ? WHERE id = ?",
            (ts, ts, existing["id"]),
        )
        prev_id = existing["id"]
    aid = new_id("attr")
    conn.execute(
        "INSERT INTO attributes (id, node_id, name, value, unit, confidence_raw, "
        "confidence_calibrated, valid_from, valid_to, status, previous_attribute_id, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)",
        (aid, node_id, name, value, unit, confidence, calibrate(confidence), ts, None, prev_id, ts, ts),
    )
    _add_attr_evidence(conn, aid, session_id, message_id, quote)


def _write_attributes(conn, node_id, dimension, assertion, session_id, message_id) -> None:
    conf = float(assertion["confidence"])
    quote = assertion.get("quote", assertion.get("value", ""))
    for attr in assertion.get("attributes") or []:
        apply_attribute(conn, node_id, dimension, attr, conf, session_id, message_id, quote)


def _active_single(conn, user_id, dimension, key):
    return conn.execute(
        "SELECT * FROM nodes WHERE user_id = ? AND dimension = ? "
        "AND key = ? AND status = ? ORDER BY created_at DESC LIMIT 1",
        (user_id, dimension, key, STATUS_ACTIVE),
    ).fetchone()


def _active_by_value(conn, user_id, dimension, key, value):
    return conn.execute(
        "SELECT * FROM nodes WHERE user_id = ? AND dimension = ? "
        "AND key = ? AND value = ? AND status = ? ORDER BY created_at DESC LIMIT 1",
        (user_id, dimension, key, value, STATUS_ACTIVE),
    ).fetchone()


def _close_node(conn, node_id, detail=None) -> None:
    """Close a node (supersede + set valid_to). If `detail` is given it also
    records *why* (ended/achieved/abandoned); otherwise status_detail is left
    as-is — a plain value replacement is not a user-driven cessation.

    A closed node's active attributes are closed with it (INV-20): the rows are
    preserved for history/audit, but an ended relationship's `since`/`name` must
    not read as a CURRENT attribute. Restoring the node reactivates them."""
    ts = now_iso()
    if detail is None:
        conn.execute("UPDATE nodes SET status = ?, valid_to = ? WHERE id = ?",
                     (STATUS_SUPERSEDED, ts, node_id))
    else:
        conn.execute("UPDATE nodes SET status = ?, status_detail = ?, valid_to = ? WHERE id = ?",
                     (STATUS_SUPERSEDED, detail, ts, node_id))
    conn.execute(
        "UPDATE attributes SET status = 'superseded', valid_to = ? "
        "WHERE node_id = ? AND status = 'active'", (ts, node_id))


def _create_node(conn, user_id, assertion, previous_id=None, model=None, message_id=None) -> str:
    node_id = new_id("node")
    raw = float(assertion["confidence"])
    ts = now_iso()
    conn.execute(
        "INSERT INTO nodes (id, user_id, dimension, key, value, "
        "confidence_raw, confidence_calibrated, valid_from, valid_to, status, "
        "status_detail, previous_node_id, model, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            node_id, user_id, assertion["dimension"], assertion["key"],
            assertion["value"], raw, calibrate(raw), ts, None, STATUS_ACTIVE,
            ontology.DETAIL_ACTIVE, previous_id, model, ts,
        ),
    )
    from . import signals  # lazy: avoids an import cycle (signals imports update)
    signals.write_signals(conn, node_id, message_id, assertion)
    return node_id


def _reinforce(conn, node, assertion, model=None, message_id=None) -> float:
    # confidence_raw is the first observation's raw signal and is IMMUTABLE after
    # insert (INV-01 / the project's hard rule). Repeated evidence accumulates
    # into the CALIBRATED confidence via Noisy-OR, so it grows toward — but never
    # reaches — 1.0 without ever rewriting the raw signal.
    obs = calibrate(float(assertion["confidence"]))
    new_cal = noisy_or(node["confidence_calibrated"], obs)
    conn.execute(
        "UPDATE nodes SET confidence_calibrated = ?, model = ? WHERE id = ?",
        (new_cal, model, node["id"]),
    )
    from . import signals  # lazy: avoids an import cycle
    signals.write_signals(conn, node["id"], message_id, assertion)
    return new_cal


# ── resolver helpers (PARTS 3/4/5/8) ────────────────────────────────────── #
_STOP = {"a", "an", "the", "my", "of", "and", "or", "with", "for", "to", "in",
         "on", "is", "are", "i", "favourite", "favorite"}


def _head_tokens(value: str) -> set[str]:
    return {w for w in ontology.normalize_value(value).split() if w not in _STOP and len(w) > 2}


def _active_nodes(conn, user_id):
    return conn.execute(
        "SELECT * FROM nodes WHERE user_id = ? AND status = 'active'", (user_id,)
    ).fetchall()


def resolve_by_value(conn, user_id, value, dimension=None, key=None):
    """Active nodes whose value matches (dimension/key optional filters)."""
    v = ontology.normalize_value(value)
    out = []
    for n in _active_nodes(conn, user_id):
        if ontology.normalize_value(n["value"]) != v:
            continue
        if dimension and n["dimension"] != dimension:
            continue
        if key and n["key"] != key:
            continue
        out.append(n)
    return out


def resolve_end(conn, user_id, dim, key, value):
    """Find the single node an END refers to. Returns (node, reason_code).
    Matches full identity; if the extractor's key is uncertain, falls back to a
    unique value match within the dimension. Never key alone."""
    if ontology.is_single(dim, key):
        node = _active_single(conn, user_id, dim, key)
        return (node, None) if node else (None, "no_matching_node")
    exact = resolve_by_value(conn, user_id, value, dim, key)
    if len(exact) == 1:
        return exact[0], None
    by_val = resolve_by_value(conn, user_id, value, dim)
    if len(by_val) == 1:
        return by_val[0], None
    if len(by_val) > 1:
        return None, "ambiguous_match"
    # Cross-dimension value-only fallback (tier-0 forced END may guess dimension).
    anywhere = resolve_by_value(conn, user_id, value)
    if len(anywhere) == 1:
        return anywhere[0], None
    if len(anywhere) > 1:
        return None, "ambiguous_match"
    return None, "no_matching_node"


def _head_noun(value: str) -> str:
    toks = [t for t in value.split() if t not in _STOP] or value.split()
    return toks[-1] if toks else value


def _inflection(a: str, b: str) -> bool:
    """b is a plural/inflection of a (or vice versa): momo/momos, degree/degrees."""
    lo, hi = sorted((a, b), key=len)
    return bool(lo) and hi.startswith(lo) and hi[len(lo):] in ("s", "es", "ing", "ed", "d")


def _same_entity(v1: str, v2: str, threshold: float) -> tuple[bool, float]:
    """(merge?, similarity). Requires a shared head noun or an inflection —
    lexical proximity alone is not enough unless it clears the threshold."""
    if v1 == v2:
        return True, 1.0
    sim = round(difflib.SequenceMatcher(None, v1, v2).ratio(), 3)
    if _head_noun(v1) == _head_noun(v2) or _inflection(v1, v2):
        return True, max(sim, threshold)
    if sim >= threshold:
        return True, sim
    return False, sim


def _find_duplicate(conn, user_id, dimension, key, value):
    """The best matching active node in the SAME (dimension, key) slot — NEVER
    across different keys. Returns (node|None, similarity)."""
    v = ontology.normalize_value(value)
    thr = settings.pipeline.resolve_similarity
    best, best_sim = None, 0.0
    for n in conn.execute(
        "SELECT * FROM nodes WHERE user_id = ? AND dimension = ? AND key = ? AND status = 'active'",
        (user_id, dimension, key),
    ).fetchall():
        ok, sim = _same_entity(ontology.normalize_value(n["value"]), v, thr)
        if ok and sim > best_sim:
            best, best_sim = n, sim
    return best, best_sim


def delete_session(conn, user_id: str, session_id: str, purge_memory: bool = False) -> dict[str, Any]:
    """Delete a chat. Ownership is verified by the caller.

    purge_memory=False : remove the chat (messages, logs, slots) but KEEP every
      learned node/attribute. Their evidence rows are detached from the session
      (session_id -> NULL) so the quotes survive as grounding.
    purge_memory=True  : also remove this chat's contribution to the graph —
      delete the evidence it produced, then delete any node/attribute that is
      left ungrounded (i.e. was known ONLY from this chat). Nodes also grounded
      by other chats are kept, just without this chat's evidence.
    """
    msg_ids = [r["id"] for r in conn.execute(
        "SELECT id FROM messages WHERE session_id = ?", (session_id,))]
    removed_nodes = removed_attrs = 0

    if purge_memory:
        node_ev = [r["node_id"] for r in conn.execute(
            "SELECT DISTINCT node_id FROM evidence WHERE session_id = ? AND node_id IS NOT NULL",
            (session_id,))]
        attr_ev = [r["attribute_id"] for r in conn.execute(
            "SELECT DISTINCT attribute_id FROM evidence WHERE session_id = ? AND attribute_id IS NOT NULL",
            (session_id,))]

        def _grounded_elsewhere(col: str, _id: str) -> bool:
            return conn.execute(
                f"SELECT COUNT(*) c FROM evidence WHERE {col} = ? "
                "AND (session_id IS NULL OR session_id != ?)", (_id, session_id)).fetchone()["c"] > 0

        orphan_nodes = [n for n in node_ev if not _grounded_elsewhere("node_id", n)]

        # This chat's evidence goes first (detaches it from kept nodes too).
        conn.execute("DELETE FROM evidence WHERE session_id = ?", (session_id,))

        for nid in orphan_nodes:
            conn.execute("UPDATE nodes SET previous_node_id = NULL WHERE previous_node_id = ?", (nid,))
            conn.execute("DELETE FROM edges WHERE source_node_id = ? OR target_node_id = ?", (nid, nid))
            aids = [r["id"] for r in conn.execute("SELECT id FROM attributes WHERE node_id = ?", (nid,))]
            for aid in aids:
                conn.execute("UPDATE attributes SET previous_attribute_id = NULL WHERE previous_attribute_id = ?", (aid,))
                conn.execute("DELETE FROM evidence WHERE attribute_id = ?", (aid,))
            conn.execute("UPDATE pending_slots SET node_id = NULL WHERE node_id = ?", (nid,))
            conn.execute("DELETE FROM evidence WHERE node_id = ?", (nid,))
            conn.execute("DELETE FROM attributes WHERE node_id = ?", (nid,))
            conn.execute("DELETE FROM nodes WHERE id = ?", (nid,))
            removed_nodes += 1

        # Orphaned attributes on surviving nodes (the node lives on other chats).
        for aid in attr_ev:
            if not conn.execute("SELECT 1 FROM attributes WHERE id = ?", (aid,)).fetchone():
                continue  # already removed with its node
            if _grounded_elsewhere("attribute_id", aid):
                continue
            conn.execute("UPDATE attributes SET previous_attribute_id = NULL WHERE previous_attribute_id = ?", (aid,))
            conn.execute("DELETE FROM evidence WHERE attribute_id = ?", (aid,))
            conn.execute("DELETE FROM attributes WHERE id = ?", (aid,))
            removed_attrs += 1
    else:
        # Keep the learned state; just detach its evidence from the gone session.
        conn.execute("UPDATE evidence SET session_id = NULL WHERE session_id = ?", (session_id,))

    conn.execute("DELETE FROM pending_slots WHERE session_id = ?", (session_id,))
    conn.execute("DELETE FROM gate_log WHERE session_id = ?", (session_id,))
    conn.execute("DELETE FROM events WHERE session_id = ?", (session_id,))
    if msg_ids:
        qm = ",".join("?" * len(msg_ids))
        conn.execute(f"DELETE FROM extraction_log WHERE message_id IN ({qm})", msg_ids)
    conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
    conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    return {"deleted": True, "session_id": session_id, "purged_memory": purge_memory,
            "removed_nodes": removed_nodes, "removed_attributes": removed_attrs}


def forget_node(conn, user_id: str, node_id: str) -> dict[str, Any]:
    """User-initiated 'forget this about me': permanently remove a fact and its
    whole supersession history (how it changed), together with the attributes,
    evidence and relations grounded on it. Ownership is verified here.

    Unlike END/supersede (which are non-destructive and reversible), this is an
    explicit erasure the user asked for, so it leaves nothing behind to restore.
    """
    node = conn.execute(
        "SELECT * FROM nodes WHERE id = ? AND user_id = ?", (node_id, user_id)).fetchone()
    if node is None:
        return {"forgotten": False, "reason": "not_found"}

    # The fact is the node plus its ancestor chain (older, superseded versions).
    chain, cur, seen = [], node, set()
    while cur is not None and cur["id"] not in seen:
        chain.append(cur["id"])
        seen.add(cur["id"])
        pid = cur["previous_node_id"]
        cur = conn.execute(
            "SELECT * FROM nodes WHERE id = ? AND user_id = ?", (pid, user_id)).fetchone() if pid else None

    for nid in chain:
        conn.execute("UPDATE nodes SET previous_node_id = NULL WHERE previous_node_id = ?", (nid,))
        conn.execute("DELETE FROM edges WHERE source_node_id = ? OR target_node_id = ?", (nid, nid))
        aids = [r["id"] for r in conn.execute("SELECT id FROM attributes WHERE node_id = ?", (nid,))]
        for aid in aids:
            conn.execute("UPDATE attributes SET previous_attribute_id = NULL WHERE previous_attribute_id = ?", (aid,))
            conn.execute("DELETE FROM evidence WHERE attribute_id = ?", (aid,))
        conn.execute("UPDATE pending_slots SET node_id = NULL WHERE node_id = ?", (nid,))
        conn.execute("DELETE FROM evidence WHERE node_id = ?", (nid,))
        conn.execute("DELETE FROM attributes WHERE node_id = ?", (nid,))
        conn.execute("DELETE FROM nodes WHERE id = ?", (nid,))

    return {"forgotten": True, "node_id": node_id, "removed_nodes": len(chain),
            "dimension": node["dimension"], "key": node["key"], "value": node["value"]}


def _cross_dimension_match(conn, user_id, dimension, value):
    """fix step 4: an active node with the SAME normalised value under a
    DIFFERENT dimension. Returns the node, unless the two dimensions are allowed
    to coexist (a language that is both a preference and a skill)."""
    v = ontology.normalize_value(value)
    for n in _active_nodes(conn, user_id):
        if n["dimension"] == dimension:
            continue
        if ontology.normalize_value(n["value"]) != v:
            continue
        if ontology.may_coexist_cross_dim(dimension, n["dimension"]):
            continue
        return n
    return None


def merge_nodes(conn, user_id, keep_id, dup_id) -> bool:
    """Collapse dup_id into keep_id: move attributes, evidence, edges, then mark
    the source `merged` (kept for audit, hidden from the graph)."""
    keep = conn.execute("SELECT * FROM nodes WHERE id = ? AND user_id = ?", (keep_id, user_id)).fetchone()
    dup = conn.execute("SELECT * FROM nodes WHERE id = ? AND user_id = ?", (dup_id, user_id)).fetchone()
    if not keep or not dup or keep_id == dup_id:
        return False
    conn.execute("UPDATE attributes SET node_id = ? WHERE node_id = ?", (keep_id, dup_id))
    conn.execute("UPDATE evidence SET node_id = ? WHERE node_id = ?", (keep_id, dup_id))
    conn.execute("UPDATE edges SET source_node_id = ? WHERE source_node_id = ?", (keep_id, dup_id))
    conn.execute("UPDATE edges SET target_node_id = ? WHERE target_node_id = ?", (keep_id, dup_id))
    conn.execute("UPDATE nodes SET previous_node_id = ? WHERE previous_node_id = ?", (keep_id, dup_id))
    conn.execute("UPDATE nodes SET status = 'merged', valid_to = ? WHERE id = ?", (now_iso(), dup_id))
    return True


def restore_node(conn, user_id: str, node_id: str, event_id: str | None = None) -> dict[str, Any]:
    """Undo (item 6): reactivate an ended/superseded node. Never deletes history;
    logs a new 'restored' event. If restoring a SINGLE-slot node would clash with
    an active sibling, that sibling is superseded."""
    node = conn.execute("SELECT * FROM nodes WHERE id = ? AND user_id = ?",
                        (node_id, user_id)).fetchone()
    if node is None:
        return {"restored": False, "reason": "not_found"}
    if node["status"] == STATUS_ACTIVE:
        return {"restored": False, "reason": "already_active"}
    if ontology.is_single(node["dimension"], node["key"]):
        other = _active_single(conn, user_id, node["dimension"], node["key"])
        if other is not None and other["id"] != node_id:
            _close_node(conn, other["id"])
    conn.execute("UPDATE nodes SET status = ?, status_detail = ?, valid_to = NULL WHERE id = ?",
                 (STATUS_ACTIVE, ontology.DETAIL_ACTIVE, node_id))
    # Reactivate attributes closed WITH the node (those with no active sibling),
    # so a restored relationship regains its since/name (mirror of _close_node).
    for a in conn.execute(
        "SELECT id, name FROM attributes WHERE node_id = ? AND status = 'superseded'", (node_id,)):
        has_active = conn.execute(
            "SELECT 1 FROM attributes WHERE node_id = ? AND name = ? AND status = 'active' LIMIT 1",
            (node_id, a["name"])).fetchone()
        if not has_active:
            conn.execute("UPDATE attributes SET status = 'active', valid_to = NULL WHERE id = ?", (a["id"],))
    conn.execute(
        "INSERT INTO events (id, session_id, message_id, action, dimension, key, value, "
        "node_id, reason_code, created_at) VALUES (?, NULL, NULL, 'restored', ?, ?, ?, ?, ?, ?)",
        (new_id("evt"), node["dimension"], node["key"], node["value"], node_id,
         f"revert:{event_id}" if event_id else "restore", now_iso()),
    )
    return {"restored": True, "node_id": node_id, "dimension": node["dimension"],
            "key": node["key"], "value": node["value"]}


def revert_event(conn, user_id: str, event_id: str) -> dict[str, Any]:
    """Restore the node a given event acted on, to its pre-event (active) state."""
    ev = conn.execute("SELECT node_id FROM events WHERE id = ?", (event_id,)).fetchone()
    if ev is None or not ev["node_id"]:
        return {"restored": False, "reason": "no_node_for_event"}
    return restore_node(conn, user_id, ev["node_id"], event_id)


def _resolve_target(conn, user_id, target):
    """Resolve an extractor target_node to an existing node, loosening filters."""
    val = str(target.get("value", ""))
    dim = str(target.get("dimension")).lower() if target.get("dimension") else None
    key = ontology.normalize_key(target.get("key")) if target.get("key") else None
    for d, k in ((dim, key), (dim, None), (None, None)):
        matches = resolve_by_value(conn, user_id, val, d, k)
        if matches:
            return matches[0]
    return None


def create_edge(conn, user_id, source_id, relation, target_id, quote, polarity="positive") -> bool:
    if not ontology.is_valid_relation(relation) or source_id == target_id:
        return False
    pol = "negative" if str(polarity).lower().startswith("neg") else "positive"
    try:
        conn.execute(
            "INSERT OR IGNORE INTO edges (id, user_id, source_node_id, relation, "
            "target_node_id, polarity, quote, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (new_id("edge"), user_id, source_id, relation, target_id, pol, quote, now_iso()),
        )
        return True
    except sqlite3.IntegrityError:
        return False


def _named_in(value: str, message: str) -> bool:
    """Every significant token of `value` appears in the source message."""
    toks = [t for t in ontology.normalize_value(value).split() if len(t) > 2]
    low = message.lower()
    return bool(toks) and all(t in low for t in toks)


def apply_relations(conn, user_id, relations, message: str):
    """Create an edge ONLY when the source message explicitly names BOTH
    endpoints (bug 2) and both resolve to existing nodes. The message is the
    justifying quote; no naming quote -> no edge. No similarity/recency linking.

    Returns (made, rejected): rejected carries a reason_code for logging."""
    made, rejected = [], []
    for rel in relations or []:
        if not isinstance(rel, dict):
            continue
        relation = str(rel.get("relation", "")).strip().lower()
        frm, to = str(rel.get("from_value", "")), str(rel.get("to_value", ""))
        polarity = str(rel.get("polarity", "positive")).strip().lower()
        if not ontology.is_valid_relation(relation):
            rejected.append({"relation": relation, "from": frm, "to": to, "reason": "invalid_relation"})
            continue
        # Both endpoints must be named in the message that produced the relation.
        if not (_named_in(frm, message) and _named_in(to, message)):
            rejected.append({"relation": relation, "from": frm, "to": to, "reason": "edge_unjustified"})
            continue
        src = resolve_by_value(conn, user_id, frm)
        tgt = resolve_by_value(conn, user_id, to)
        if len(src) == 1 and len(tgt) == 1 and create_edge(
            conn, user_id, src[0]["id"], relation, tgt[0]["id"], message, polarity
        ):
            made.append({"from": src[0]["value"], "relation": relation, "to": tgt[0]["value"],
                         "polarity": "negative" if polarity.startswith("neg") else "positive"})
        else:
            rejected.append({"relation": relation, "from": frm, "to": to, "reason": "endpoint_missing"})
    return made, rejected


def _change(action, node, assertion, previous_id=None, conf=None, reason_code=None,
            matched_label=None, similarity=None) -> dict[str, Any]:
    return {
        "action": action,
        "node_id": node,
        "dimension": assertion["dimension"],
        "key": assertion["key"],
        "value": assertion["value"],
        "previous_node_id": previous_id,
        "confidence_calibrated": conf,
        "reason_code": reason_code,
        "matched_label": matched_label,
        "similarity": similarity,
    }


def _label(n) -> str:
    return f"{n['dimension']}/{n['key']}={n['value']}"


# ── main entry point ────────────────────────────────────────────────────── #
def apply_assertion(
    conn: sqlite3.Connection,
    user_id: str,
    session_id: str,
    message_id: str,
    assertion: dict[str, Any],
    model: str | None = None,
    qa_resolved: bool = False,
) -> dict[str, Any]:
    """Apply one assertion to the user's graph. Returns a change record.

    `model` is the resolved extractor model that produced the assertion; it is
    recorded on any node created or reinforced. `qa_resolved` marks an assertion
    derived from a Q-A-rewritten fragment — it may add facts but is never allowed
    to trigger a destructive operation (END / supersede).
    """
    dim = assertion["dimension"]
    key = assertion["key"]
    value = assertion["value"]
    op = assertion.get("operation", "ADD")
    replaces = assertion.get("replaces_value")
    quote = assertion.get("quote", value)
    single = ontology.is_single(dim, key)

    # ── PART 2: entity subject — the fact belongs to a related entity, not the
    # user. Resolve the entity (create it if absent), attach attributes to it. ── #
    subject = assertion.get("subject")
    if isinstance(subject, dict) and assertion.get("attributes"):
        ent = _resolve_target(conn, user_id, subject)
        if ent is None:
            ent_assertion = {
                "dimension": subject["dimension"], "key": subject["key"],
                "value": subject.get("value") or subject["key"],
                "confidence": assertion.get("confidence", 0.7),
            }
            ent_id = _create_node(conn, user_id, ent_assertion, model=model, message_id=message_id)
            _add_evidence(conn, ent_id, session_id, message_id, quote)
            ent_dim, action = subject["dimension"], "created"
        else:
            ent_id, ent_dim, action = ent["id"], ent["dimension"], "refined"
        _write_attributes(conn, ent_id, ent_dim, assertion, session_id, message_id)
        return _change(action, ent_id, {
            "dimension": ent_dim, "key": subject["key"],
            "value": subject.get("value") or subject["key"],
        })

    # ── PART 4: assertion attaches an attribute to an existing node ──────── #
    target = assertion.get("target_node")
    if target and isinstance(target, dict) and assertion.get("attributes"):
        tnode = _resolve_target(conn, user_id, target)
        if tnode is None:
            return _change("ignored", None, assertion, reason_code="attribute_orphaned")
        _write_attributes(conn, tnode["id"], tnode["dimension"], assertion, session_id, message_id)
        return _change("refined", tnode["id"], assertion)

    # ── END: close the matching node, create nothing ────────────────────── #
    if op == "END":
        node, rc = resolve_end(conn, user_id, dim, key, value)
        if node is None:
            return _change("ignored", None, assertion, reason_code=rc)
        ok, reason = _destructive_ok("end", node, assertion, qa_resolved)
        if not ok:
            _queue_confirmation(conn, user_id, session_id, message_id, "end", node, assertion, reason)
            return _change("ignored", node["id"], assertion, reason_code="destructive_blocked",
                           matched_label=_label(node))
        _close_node(conn, node["id"], DETAIL_ENDED)
        return _change("ended", node["id"], assertion, node["previous_node_id"])

    # ── explicit replacement: end the named old value, add the new ──────── #
    if replaces:
        old = (
            _active_single(conn, user_id, dim, key)
            if single
            else _active_by_value(conn, user_id, dim, key, replaces)
        )
        prev_id = None
        if old is not None:
            ok, reason = _destructive_ok("supersede", old, assertion, qa_resolved)
            if not ok:
                _queue_confirmation(conn, user_id, session_id, message_id, "supersede", old, assertion, reason)
                return _change("ignored", old["id"], assertion, reason_code="destructive_blocked",
                               matched_label=_label(old))
            _close_node(conn, old["id"], DETAIL_ENDED)
            prev_id = old["id"]
        node_id = _create_node(conn, user_id, assertion, previous_id=prev_id, model=model, message_id=message_id)
        _add_evidence(conn, node_id, session_id, message_id, quote)
        _write_attributes(conn, node_id, dim, assertion, session_id, message_id)
        return _change("superseded" if prev_id else "created", node_id, assertion, prev_id)

    # ── SINGLE slot ─────────────────────────────────────────────────────── #
    if single:
        existing = _active_single(conn, user_id, dim, key)
        if existing is not None and ontology.normalize_value(existing["value"]) == value:
            conf = _reinforce(conn, existing, assertion, model=model, message_id=message_id)
            _add_evidence(conn, existing["id"], session_id, message_id, quote)
            _write_attributes(conn, existing["id"], dim, assertion, session_id, message_id)
            return _change("reinforced", existing["id"], assertion, existing["previous_node_id"], conf,
                           matched_label=_label(existing), similarity=1.0)
        prev_id = None
        if existing is not None:  # conflict — supersede (guarded: needs explicit replacement)
            ok, reason = _destructive_ok("supersede", existing, assertion, qa_resolved)
            if not ok:
                _queue_confirmation(conn, user_id, session_id, message_id, "supersede", existing, assertion, reason)
                return _change("ignored", existing["id"], assertion, reason_code="destructive_blocked",
                               matched_label=_label(existing))
            _close_node(conn, existing["id"])
            prev_id = existing["id"]
        node_id = _create_node(conn, user_id, assertion, previous_id=prev_id, model=model, message_id=message_id)
        _add_evidence(conn, node_id, session_id, message_id, quote)
        _write_attributes(conn, node_id, dim, assertion, session_id, message_id)
        return _change("superseded" if prev_id else "created", node_id, assertion, prev_id)

    # ── MULTI slot ─────────────────────────────────────────────────────── #
    # Resolve ONLY within the same (dimension, key), and only on a shared head
    # noun / inflection above resolve_similarity. Never merge across keys (bug 1).
    dup, sim = _find_duplicate(conn, user_id, dim, key, value)
    if dup is not None:
        conf = _reinforce(conn, dup, assertion, model=model, message_id=message_id)
        _add_evidence(conn, dup["id"], session_id, message_id, quote)
        _write_attributes(conn, dup["id"], dup["dimension"], assertion, session_id, message_id)
        return _change("reinforced", dup["id"], assertion, dup["previous_node_id"], conf,
                       matched_label=_label(dup), similarity=sim)
    # fix step 4: the same value under another dimension is a duplicate (unless
    # the dimensions may coexist) — refine the existing node, don't create anew.
    xdup = _cross_dimension_match(conn, user_id, dim, value)
    if xdup is not None:
        conf = _reinforce(conn, xdup, assertion, model=model, message_id=message_id)
        _add_evidence(conn, xdup["id"], session_id, message_id, quote)
        return _change("reinforced", xdup["id"], assertion, xdup["previous_node_id"], conf,
                       reason_code="dimension_conflict", matched_label=_label(xdup), similarity=1.0)
    node_id = _create_node(conn, user_id, assertion, model=model, message_id=message_id)
    _add_evidence(conn, node_id, session_id, message_id, quote)
    _write_attributes(conn, node_id, dim, assertion, session_id, message_id)
    return _change("created", node_id, assertion)
