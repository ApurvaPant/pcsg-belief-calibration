"""Dashboard data contract (STEP 1). dashboard.json is the SINGLE source both the
paper figures (matplotlib) and the Results tab read, so they cannot disagree.

`validate(d)` returns a list of human-readable errors (empty = valid). It is a
light structural check (no external jsonschema dep): required keys + types.
"""
from __future__ import annotations

from typing import Any

# Fixed colours — ONE per system / signal, shared by figures and the UI.
SYSTEM_COLORS = {
    "pcsg": "#5b6cff", "no_memory": "#9aa6b8", "vector_rag": "#38c7e8",
    "full_context": "#f2c558", "mem0": "#b197fc",
}
SIGNAL_COLORS = {"verbalized": "#5b6cff", "self_consistency": "#5cd39a", "evidence": "#fb8a8a"}

TOKEN_GROWTH_TURNS = [5, 10, 15, 20, 25]


def _is(d, key, types, errs, path):
    if key not in d:
        errs.append(f"{path}: missing '{key}'")
        return False
    if not isinstance(d[key], types):
        errs.append(f"{path}.{key}: expected {types}, got {type(d[key]).__name__}")
        return False
    return True


def validate(d: dict[str, Any]) -> list[str]:
    e: list[str] = []
    if not isinstance(d, dict):
        return ["top level must be an object"]
    # meta
    if _is(d, "meta", dict, e, "root"):
        for k in ("run_id", "git_sha", "mode", "date"):
            _is(d["meta"], k, str, e, "meta")
        _is(d["meta"], "models", dict, e, "meta")
        _is(d["meta"], "sample_sizes", dict, e, "meta")
    if "watermark" in d and d["watermark"] is not None and not isinstance(d["watermark"], str):
        e.append("watermark: must be string or null")
    # sections (each must exist; may be empty when a stage hasn't run)
    _is(d, "kpi", dict, e, "root")
    _is(d, "baselines", list, e, "root")
    if _is(d, "token_growth", list, e, "root"):
        for i, pt in enumerate(d["token_growth"]):
            for k in ("turn", "raw", "pcsg"):
                _is(pt, k, (int, float, dict), e, f"token_growth[{i}]")
    _is(d, "gate_funnel", dict, e, "root")
    _is(d, "cost_curve", dict, e, "root")
    _is(d, "calibration", dict, e, "root")
    _is(d, "abstention", dict, e, "root")
    _is(d, "persona_replay", list, e, "root")
    return e
