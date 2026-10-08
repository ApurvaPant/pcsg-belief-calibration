"""Assertion vocabulary (§1.4).

Each assertion is evaluated against a context:
    curr   — current /debug/snapshot
    prev   — snapshot before this turn
    turn   — {changes, salient, salience_score, badge, reply, writes}
    judge  — optional callable(rubric, reply) -> (ok, reason) for reply_judge

Returns a list of (name, ok, detail) tuples.
"""
from __future__ import annotations

from typing import Any, Callable

from app import ontology

Ctx = dict[str, Any]


def _nz(v: Any) -> str:
    return ontology.normalize_value(str(v or ""))


def _active_nodes(curr) -> list[dict]:
    return [n for n in curr.get("nodes", []) if n.get("status") == "active"]


def _all_nodes(curr) -> list[dict]:
    return curr.get("nodes", [])


def _match_node(n: dict, sel: dict) -> bool:
    if "dim" in sel and n["dimension"] != sel["dim"]:
        return False
    if "key" in sel and n["key"] != sel["key"]:
        return False
    if "key_any" in sel and n["key"] not in sel["key_any"]:
        return False
    if "value" in sel and _nz(n["value"]) != _nz(sel["value"]):
        return False
    if "value_any" in sel and _nz(n["value"]) not in {_nz(x) for x in sel["value_any"]}:
        return False
    if "status" in sel and n.get("status") != sel["status"]:
        return False
    return True


def _resolve(curr, sel: dict, pool=None):
    pool = pool if pool is not None else _active_nodes(curr)
    return [n for n in pool if _match_node(n, sel)]


def _attrs_for(curr, node_id, active_only=True):
    return [a for a in curr.get("attributes", [])
            if a.get("node_id") == node_id and (not active_only or a.get("status") == "active")]


# ── individual assertions ──────────────────────────────────────────────────#
def a_node(curr, sel, out):
    pool = _all_nodes(curr) if sel.get("status") and sel["status"] != "active" else _active_nodes(curr)
    matches = _resolve(curr, {k: v for k, v in sel.items() if k not in ("attributes",)}, pool)
    if len(matches) != 1:
        out.append(("node", False, f"expected exactly 1 node for {sel}, found {len(matches)}"))
        return
    out.append(("node", True, f"{sel}"))
    for aname, aval in (sel.get("attributes") or {}).items():
        base = aname[:-4] if aname.endswith("_any") else aname
        got = _attrs_for(curr, matches[0]["id"])
        want = {_nz(x) for x in aval} if aname.endswith("_any") else {_nz(aval)}
        ok = any(a["name"] == base and _nz(a["value"]) in want for a in got)
        out.append((f"node.attr[{base}]", ok,
                    f"want {base} in {want}; have {[(a['name'], a['value']) for a in got]}"))


def a_absent_node(curr, sel, out):
    matches = _resolve(curr, sel)
    out.append(("absent_node", len(matches) == 0, f"{sel} -> {len(matches)} active matches"))


def a_node_count(curr, sel, out):
    pool = _all_nodes(curr) if sel.get("status") and sel["status"] != "active" else _active_nodes(curr)
    sel2 = {k: v for k, v in sel.items() if k in ("dim", "key", "key_any", "value", "value_any", "status")}
    n = len(_resolve(curr, sel2, pool))
    for op, cmp in (("eq", lambda a, b: a == b), ("lte", lambda a, b: a <= b), ("gte", lambda a, b: a >= b)):
        if op in sel:
            out.append((f"node_count.{op}", cmp(n, sel[op]), f"count={n} {op} {sel[op]}"))


def a_attribute(curr, sel, out):
    matches = _resolve(curr, sel["node"])
    if len(matches) != 1:
        out.append(("attribute", False, f"selector {sel['node']} -> {len(matches)} (ambiguous)"))
        return
    got = _attrs_for(curr, matches[0]["id"], active_only=(sel.get("status", "active") == "active"))
    want = {_nz(x) for x in sel["value_any"]} if "value_any" in sel else {_nz(sel.get("value"))}
    ok = any(a["name"] == sel["name"] and (_nz(a["value"]) in want or "value" not in sel and "value_any" not in sel)
             for a in got)
    out.append(("attribute", ok, f"{sel['name']} want {want}; have {[(a['name'], a['value']) for a in got]}"))


def a_status(curr, sel, out):
    matches = _resolve(curr, sel["node"], _all_nodes(curr))
    if not matches:
        out.append(("status", False, f"no node for {sel['node']}"))
        return
    # prefer the newest row for this slot
    node = sorted(matches, key=lambda r: r.get("created_at", ""))[-1]
    want = sel["is"]
    actual = "ended" if (node["status"] == "superseded" and node.get("status_detail") == "ended") else node["status"]
    out.append(("status", actual == want, f"{sel['node']} is {actual}, want {want}"))


def a_history_chain(curr, sel, out):
    matches = _resolve(curr, sel["node"], _all_nodes(curr))
    if not matches:
        out.append(("history_chain", False, f"no node for {sel['node']}"))
        return
    dim = matches[0]["dimension"]
    key = matches[0]["key"]
    chain = sorted([n for n in _all_nodes(curr) if n["dimension"] == dim and n["key"] == key],
                   key=lambda r: r.get("created_at", ""))
    got = [_nz(n["value"]) for n in chain]
    want = [_nz(x) for x in sel["values"]]
    out.append(("history_chain", got == want, f"chain {got} want {want}"))


def a_edge(curr, sel, out, absent=False):
    src = _resolve(curr, sel["src"], _all_nodes(curr))
    dst = _resolve(curr, sel["dst"], _all_nodes(curr))
    rels = sel.get("rel_any", [sel["rel"]] if "rel" in sel else None)
    found = []
    for e in curr.get("edges", []):
        if src and e["source_node_id"] not in {n["id"] for n in src}:
            continue
        if dst and e["target_node_id"] not in {n["id"] for n in dst}:
            continue
        if rels and e["relation"] not in rels:
            continue
        if "polarity" in sel and e.get("polarity") != sel["polarity"]:
            continue
        found.append(e)
    if absent:
        out.append(("absent_edge", len(found) == 0, f"{sel} -> {len(found)}"))
    else:
        out.append(("edge", len(found) >= 1, f"{sel} -> {len(found)}"))


def a_unchanged(curr, prev, selectors, out):
    for sel in selectors:
        cm = _resolve(curr, sel, _all_nodes(curr))
        pm = _resolve(prev, sel, _all_nodes(prev))
        if len(cm) != 1 or len(pm) != 1:
            out.append(("unchanged", False, f"{sel} not uniquely resolvable (prev {len(pm)}, curr {len(cm)})"))
            continue
        c, p = cm[0], pm[0]
        ok = (c["value"] == p["value"] and c["status"] == p["status"]
              and c["confidence_raw"] == p["confidence_raw"])
        out.append(("unchanged", ok, f"{sel} value/status/conf stable" if ok else f"{sel} changed"))


def _badge(turn):
    return turn.get("badge")


def check_clause(clause: dict, ctx: Ctx) -> list[tuple[str, bool, str]]:
    """Evaluate one expect/expect_final clause (may hold several assertions)."""
    out: list[tuple[str, bool, str]] = []
    curr, prev, turn = ctx["curr"], ctx.get("prev", {}), ctx.get("turn", {})

    if "node" in clause:
        a_node(curr, clause["node"] | {"attributes": clause.get("attributes")}
               if clause.get("attributes") else clause["node"], out)
    if "absent_node" in clause:
        a_absent_node(curr, clause["absent_node"], out)
    if "node_count" in clause:
        a_node_count(curr, clause["node_count"], out)
    if "attribute" in clause:
        a_attribute(curr, clause["attribute"], out)
    if "status" in clause:
        a_status(curr, clause["status"], out)
    if "history_chain" in clause:
        a_history_chain(curr, clause["history_chain"], out)
    if "edge" in clause:
        a_edge(curr, clause["edge"], out)
    if "absent_edge" in clause:
        a_edge(curr, clause["absent_edge"], out, absent=True)
    if "unchanged" in clause:
        a_unchanged(curr, prev, clause["unchanged"], out)

    if "writes" in clause:
        out.append(("writes", turn.get("writes", 0) == clause["writes"],
                    f"writes={turn.get('writes', 0)} want {clause['writes']}"))
    if "no_events_of_type" in clause:
        acts = {c.get("action") for c in turn.get("changes", [])}
        banned = set(clause["no_events_of_type"])
        hit = acts & {x.replace("node_", "") for x in banned} | (acts & banned)
        out.append(("no_events_of_type", not hit, f"saw {acts}, banned {banned}"))
    if "badge_in" in clause:
        out.append(("badge_in", _badge(turn) in clause["badge_in"], f"badge={_badge(turn)} want in {clause['badge_in']}"))
    if "badge_not_in" in clause:
        out.append(("badge_not_in", _badge(turn) not in clause["badge_not_in"],
                    f"badge={_badge(turn)} not in {clause['badge_not_in']}"))
    if "salience" in clause:
        s = turn.get("salience_score", 0.0) or 0.0
        if "gte" in clause["salience"]:
            out.append(("salience.gte", s >= clause["salience"]["gte"], f"salience={s}"))
        if "lte" in clause["salience"]:
            out.append(("salience.lte", s <= clause["salience"]["lte"], f"salience={s}"))
    if "reason_code" in clause:
        codes = {c.get("reason_code") for c in turn.get("changes", [])}
        out.append(("reason_code", clause["reason_code"] in codes, f"codes={codes} want {clause['reason_code']}"))
    if "reply_contains_any" in clause:
        rep = (turn.get("reply") or "").lower()
        out.append(("reply_contains_any", any(w.lower() in rep for w in clause["reply_contains_any"]),
                    f"reply={rep[:80]!r}"))
    if "reply_not_contains" in clause:
        rep = (turn.get("reply") or "").lower()
        bad = [w for w in clause["reply_not_contains"] if w.lower() in rep]
        out.append(("reply_not_contains", not bad, f"found {bad} in reply"))
    if "reply_judge" in clause:
        judge = ctx.get("judge")
        if judge is None:
            out.append(("reply_judge", None, f"skipped (no judge/mock): {clause['reply_judge']}"))
        else:
            ok, reason = judge(clause["reply_judge"], turn.get("reply") or "")
            out.append(("reply_judge", ok, f"{clause['reply_judge']} -> {reason}"))
    return out
