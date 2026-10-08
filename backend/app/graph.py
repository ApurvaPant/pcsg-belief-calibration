"""Read-side: build the user-scoped Cytoscape graph and node detail views."""
from __future__ import annotations

from typing import Any

from .db import get_conn


def _node_data(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "dimension": row["dimension"],
        "key": row["key"],
        "value": row["value"],
        "confidence_raw": row["confidence_raw"],
        "confidence_calibrated": row["confidence_calibrated"],
        "valid_from": row["valid_from"],
        "valid_to": row["valid_to"],
        "status": row["status"],
        "status_detail": row["status_detail"] if "status_detail" in row.keys() else "active",
        "previous_node_id": row["previous_node_id"],
        "model": row["model"] if "model" in row.keys() else None,
        "created_at": row["created_at"],
        "label": f"{row['key']}: {row['value']}",
    }


def build_graph(user_id: str) -> dict[str, Any]:
    """Return {nodes, edges} in Cytoscape element format for a user.

    Edges represent supersession: a new node -> the node it replaced.
    """
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM nodes WHERE user_id = ? AND status != 'merged' ORDER BY created_at ASC",
            (user_id,),
        ).fetchall()

    ids = {r["id"] for r in rows}
    nodes = [{"data": _node_data(r)} for r in rows]
    edges = [
        {
            "data": {
                "id": f"e_{r['previous_node_id']}_{r['id']}",
                "source": r["id"],
                "target": r["previous_node_id"],
                "relation": "supersedes",
            }
        }
        for r in rows
        if r["previous_node_id"] and r["previous_node_id"] in ids
    ]

    # PART 8: typed inter-node relations.
    with get_conn() as conn:
        rel_rows = conn.execute(
            "SELECT * FROM edges WHERE user_id = ?", (user_id,),
        ).fetchall()
    for e in rel_rows:
        if e["source_node_id"] in ids and e["target_node_id"] in ids:
            pol = e["polarity"] if "polarity" in e.keys() else "positive"
            edges.append({"data": {
                "id": e["id"], "source": e["source_node_id"], "target": e["target_node_id"],
                "relation": e["relation"], "kind": "relation", "polarity": pol or "positive",
            }})

    return {"user_id": user_id, "nodes": nodes, "edges": edges}


def build_memory(user_id: str) -> dict[str, Any]:
    """Read-side view for the 'what the system knows about you' page: every
    non-merged node enriched with its active attributes, a representative quote,
    and an evidence count — in one round-trip so the page needs no N+1 fetches.
    The frontend groups by dimension and separates active from past."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM nodes WHERE user_id = ? AND status != 'merged' "
            "ORDER BY dimension ASC, created_at DESC",
            (user_id,),
        ).fetchall()
        items = []
        for r in rows:
            attrs = conn.execute(
                "SELECT name, value, unit FROM attributes "
                "WHERE node_id = ? AND status = 'active' ORDER BY created_at ASC",
                (r["id"],),
            ).fetchall()
            quote = conn.execute(
                "SELECT quote FROM evidence WHERE node_id = ? ORDER BY created_at DESC LIMIT 1",
                (r["id"],),
            ).fetchone()
            ev_count = conn.execute(
                "SELECT COUNT(*) c FROM evidence WHERE node_id = ?", (r["id"],),
            ).fetchone()["c"]
            item = _node_data(r)
            item["attributes"] = [
                {"name": a["name"], "value": a["value"], "unit": a["unit"]} for a in attrs
            ]
            item["quote"] = quote["quote"] if quote else None
            item["evidence_count"] = ev_count
            items.append(item)

    active = sum(1 for i in items if i["status"] == "active")
    return {
        "user_id": user_id,
        "nodes": items,
        "counts": {"total": len(items), "active": active, "past": len(items) - active},
    }


def get_node_detail(node_id: str) -> dict[str, Any] | None:
    """Return a node with its evidence quotes (attributed to the session they
    came from) and its full history chain."""
    with get_conn() as conn:
        node = conn.execute("SELECT * FROM nodes WHERE id = ?", (node_id,)).fetchone()
        if node is None:
            return None

        evidence = conn.execute(
            "SELECT e.id, e.session_id, e.message_id, e.quote, e.created_at, "
            "s.title AS session_title "
            "FROM evidence e LEFT JOIN sessions s ON s.id = e.session_id "
            "WHERE e.node_id = ? ORDER BY e.created_at ASC",
            (node_id,),
        ).fetchall()

        history = conn.execute(
            "SELECT * FROM nodes WHERE user_id = ? AND dimension = ? AND key = ? "
            "ORDER BY created_at DESC",
            (node["user_id"], node["dimension"], node["key"]),
        ).fetchall()

        attributes = conn.execute(
            "SELECT id, name, value, unit, confidence_calibrated, status, valid_from, valid_to "
            "FROM attributes WHERE node_id = ? ORDER BY created_at ASC",
            (node_id,),
        ).fetchall()

    detail = _node_data(node)
    detail["attributes"] = [
        {
            "id": a["id"], "name": a["name"], "value": a["value"], "unit": a["unit"],
            "confidence_calibrated": a["confidence_calibrated"], "status": a["status"],
            "valid_from": a["valid_from"], "valid_to": a["valid_to"],
        }
        for a in attributes
    ]
    detail["evidence"] = [
        {
            "id": e["id"],
            "session_id": e["session_id"],
            "session_title": e["session_title"],
            "message_id": e["message_id"],
            "quote": e["quote"],
            "created_at": e["created_at"],
        }
        for e in evidence
    ]
    detail["history"] = [_node_data(h) for h in history]
    return detail
