"""Small shared helpers."""
from __future__ import annotations

import json
import re
import uuid
from typing import Any

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def parse_json(text: str) -> Any:
    """Best-effort JSON parse from an LLM response.

    Handles fenced code blocks and leading/trailing prose by falling back to
    the first balanced object/array substring. Returns None on failure.
    """
    if not text:
        return None
    candidate = text.strip()

    fence = _FENCE_RE.search(candidate)
    if fence:
        candidate = fence.group(1).strip()

    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass

    # Models sometimes wrap JSON in prose or emit a trailing stray brace
    # ('...}}'). Extract the first BALANCED object/array and parse that.
    sub = _first_balanced(candidate)
    if sub:
        try:
            return json.loads(sub)
        except json.JSONDecodeError:
            pass

    # Last resort: greedy span.
    for opener, closer in (("{", "}"), ("[", "]")):
        start = candidate.find(opener)
        end = candidate.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(candidate[start : end + 1])
            except json.JSONDecodeError:
                continue
    return None


def _first_balanced(text: str) -> str | None:
    """Return the first balanced {...} or [...] substring, string-aware."""
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start = text.find(open_ch)
        if start == -1:
            continue
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == open_ch:
                depth += 1
            elif ch == close_ch:
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
    return None
