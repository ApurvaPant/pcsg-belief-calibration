"""Global invariants (§2) checked after every turn.

Each checker takes the previous and current `/debug/snapshot` dumps plus the
turn's `/chat` result and returns a list of violation strings (empty = ok).

Architecture note: this PCSG build reconciles SYNCHRONOUSLY (the chat reply is
returned after the write path) and has no `confidence_signals` table, no LLM
cache and no span/structuring split. The invariants that assume that machinery
are therefore not applicable and are listed in `NOT_APPLICABLE` with the reason,
rather than silently skipped.
"""
from __future__ import annotations

import re
from typing import Any

from app import ontology

Snapshot = dict[str, list[dict[str, Any]]]

_PREPOSITION_PHRASES = (" with ", " over ", " from ", " for ", " about ", " that ")
_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_WRITE_ACTIONS = {"created", "superseded", "reinforced", "ended", "refined", "linked"}

NOT_APPLICABLE = {
    "INV-17": "chat-call token logging is partial; extraction calls are logged",
    "INV-19": "chat prompt is not persisted, so the state block cannot be re-read",
    "INV-22": "pipeline is synchronous; the reply is produced after reconciliation",
}


def _by_id(rows: list[dict]) -> dict[str, dict]:
    return {r["id"]: r for r in rows}


def _active(snap: Snapshot, table: str) -> list[dict]:
    return [r for r in snap.get(table, []) if r.get("status") == "active"]


def check_all(prev: Snapshot, curr: Snapshot, turn: dict | None = None) -> list[str]:
    v: list[str] = []
    nodes = curr.get("nodes", [])
    attrs = curr.get("attributes", [])
    node_by_id = _by_id(nodes)

    # INV-01: confidence_raw immutable for rows present in both snapshots.
    for tbl in ("nodes", "attributes"):
        prev_by = _by_id(prev.get(tbl, []))
        for r in curr.get(tbl, []):
            p = prev_by.get(r["id"])
            if p is not None and p.get("confidence_raw") != r.get("confidence_raw"):
                v.append(f"INV-01 {tbl} {r['id']} confidence_raw changed {p['confidence_raw']}->{r['confidence_raw']}")

    # INV-02: confidence_signals append-only (never updated or deleted).
    ps, cs = _by_id(prev.get("confidence_signals", [])), _by_id(curr.get("confidence_signals", []))
    if len(curr.get("confidence_signals", [])) < len(prev.get("confidence_signals", [])):
        v.append("INV-02 confidence_signals count decreased")
    for sid, row in ps.items():
        if sid in cs and cs[sid] != row:
            v.append(f"INV-02 confidence_signal {sid} mutated")

    # INV-03: events append-only (count non-decreasing; existing rows identical).
    pe, ce = _by_id(prev.get("events", [])), _by_id(curr.get("events", []))
    if len(curr.get("events", [])) < len(prev.get("events", [])):
        v.append("INV-03 events count decreased")
    for eid, row in pe.items():
        if eid in ce and ce[eid] != row:
            v.append(f"INV-03 event {eid} mutated")

    # INV-04: no active node/attr with an empty / whitespace value.
    for tbl in ("nodes", "attributes"):
        for r in _active(curr, tbl):
            if not str(r.get("value") or "").strip():
                v.append(f"INV-04 {tbl} {r['id']} empty value")

    # INV-05: value <= 4 words, no prepositional phrase.
    for r in _active(curr, "nodes"):
        val = str(r.get("value") or "")
        if len(val.split()) > 4:
            v.append(f"INV-05 node {r['id']} value >4 words: {val!r}")
        if any(p in f" {val.lower()} " for p in _PREPOSITION_PHRASES):
            v.append(f"INV-05 node {r['id']} prepositional phrase: {val!r}")

    # INV-06: key not 'general' and matches the key pattern.
    for r in nodes:
        k = str(r.get("key") or "")
        if k == "general" or not _KEY_RE.match(k):
            v.append(f"INV-06 node {r['id']} bad key {k!r}")

    # INV-07: every active node/attr has >=1 evidence row.
    ev_nodes = {e["node_id"] for e in curr.get("evidence", []) if e.get("node_id")}
    ev_attrs = {e["attribute_id"] for e in curr.get("evidence", []) if e.get("attribute_id")}
    for r in _active(curr, "nodes"):
        if r["id"] not in ev_nodes:
            v.append(f"INV-07 active node {r['id']} ({r['dimension']}/{r['key']}) has no evidence")
    for r in _active(curr, "attributes"):
        if r["id"] not in ev_attrs:
            v.append(f"INV-07 active attribute {r['id']} ({r['name']}) has no evidence")

    # INV-08: evidence quote is a substring of the message it points to (best
    # effort — seeded rows have synthetic message ids with no stored message).
    msg_by_id = _by_id(curr.get("messages", []))
    for e in curr.get("evidence", []):
        m = msg_by_id.get(e.get("message_id"))
        if not m:
            continue
        q = str(e.get("quote") or "").lower().strip()
        hay = f"{m.get('content','')}\n{m.get('resolved_content') or ''}".lower()
        if q and q not in hay:
            v.append(f"INV-08 evidence {e['id']} quote not in its message: {q!r}")

    # INV-09: edge justification mentions both endpoints (by value token).
    for e in curr.get("edges", []):
        src = node_by_id.get(e.get("source_node_id"))
        dst = node_by_id.get(e.get("target_node_id"))
        quote = str(e.get("quote") or "").lower()
        for endp in (src, dst):
            if endp:
                toks = [t for t in ontology.normalize_value(endp["value"]).split() if len(t) > 2]
                if toks and not any(t in quote for t in toks) \
                        and not any(t in quote for t in endp["key"].split("_") if len(t) > 2):
                    v.append(f"INV-09 edge {e['id']} quote omits endpoint {endp['value']!r}")

    # INV-10: no two active nodes share (user, dim, key, normalised value).
    seen: dict[tuple, str] = {}
    for r in _active(curr, "nodes"):
        sig = (r["user_id"], r["dimension"], r["key"], ontology.normalize_value(r["value"]))
        if sig in seen:
            v.append(f"INV-10 duplicate active node {sig}")
        seen[sig] = r["id"]

    # INV-11: SINGLE (dim,key) has <=1 active node per user.
    single_count: dict[tuple, int] = {}
    for r in _active(curr, "nodes"):
        if ontology.is_single(r["dimension"], r["key"]):
            key = (r["user_id"], r["dimension"], r["key"])
            single_count[key] = single_count.get(key, 0) + 1
    for key, n in single_count.items():
        if n > 1:
            v.append(f"INV-11 SINGLE slot {key} has {n} active nodes")

    # INV-12: previous_node_id points to a superseded/ended node; chain acyclic.
    for r in nodes:
        pid = r.get("previous_node_id")
        if pid:
            p = node_by_id.get(pid)
            if p is None:
                v.append(f"INV-12 node {r['id']} previous_node_id dangling {pid}")
            elif p.get("status") not in ("superseded", "deleted"):
                v.append(f"INV-12 node {r['id']} predecessor {pid} status {p.get('status')} not closed")
    # acyclic
    for r in nodes:
        seen_ids, cur = set(), r
        while cur and cur.get("previous_node_id"):
            if cur["id"] in seen_ids:
                v.append(f"INV-12 cycle through {r['id']}")
                break
            seen_ids.add(cur["id"])
            cur = node_by_id.get(cur["previous_node_id"])

    # INV-13: active identity node evidence looks like a self-reference. A
    # Q-A-resolved fragment is checked against its message's resolved utterance
    # too (consistent with INV-08), so "not at all" -> "i'm not a morning person"
    # counts as self-referential.
    for r in _active(curr, "nodes"):
        if r["dimension"] == "identity":
            evs = [e for e in curr.get("evidence", []) if e.get("node_id") == r["id"]]
            texts = []
            for e in evs:
                texts.append(e["quote"])
                m = msg_by_id.get(e.get("message_id"))
                if m and m.get("resolved_content"):
                    texts.append(m["resolved_content"])
            check = ontology.is_self_identity_quote if r["key"] == "name" else ontology.mentions_self
            if evs and not any(check(t) for t in texts):
                v.append(f"INV-13 identity/{r['key']} evidence not self-referential: {[e['quote'] for e in evs]!r}")

    # INV-16: each user message has exactly one gate_log row.
    gate_by_msg: dict[str, int] = {}
    for g in curr.get("gate_log", []):
        gate_by_msg[g.get("message_id")] = gate_by_msg.get(g.get("message_id"), 0) + 1
    for m in curr.get("messages", []):
        if m.get("role") == "user":
            n = gate_by_msg.get(m["id"], 0)
            if n != 1:
                v.append(f"INV-16 user message {m['id']} has {n} gate_log rows")

    # INV-18: all confidences within [0,1].
    for tbl in ("nodes", "attributes"):
        for r in curr.get(tbl, []):
            for col in ("confidence_raw", "confidence_calibrated"):
                x = r.get(col)
                if x is not None and not (0.0 <= float(x) <= 1.0):
                    v.append(f"INV-18 {tbl} {r['id']} {col}={x} out of range")

    # INV-20: no active attribute on an ended/superseded node (non-historical).
    for a in _active(curr, "attributes"):
        n = node_by_id.get(a.get("node_id"))
        if n and n.get("status") != "active":
            v.append(f"INV-20 active attribute {a['id']} on {n['status']} node {n['id']}")

    # INV-21: every attribute node_id points to an existing node.
    for a in curr.get("attributes", []):
        if a.get("node_id") not in node_by_id:
            v.append(f"INV-21 attribute {a['id']} orphaned (node_id={a.get('node_id')})")

    # INV-15: a RETRIEVAL_QUERY turn produced zero writes.
    if turn and turn.get("badge") == "RETRIEVAL_QUERY" and turn.get("writes", 0) != 0:
        v.append(f"INV-15 retrieval query wrote {turn['writes']} times")

    return v
