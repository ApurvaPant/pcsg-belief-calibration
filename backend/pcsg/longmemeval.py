"""LongMemEval adapter (STEP 3 / table T5, Tier 3).

Downloads the ORACLE file of `xiaowu0162/longmemeval-cleaned` at a PINNED commit
(no paid API cost), selects the question types this study uses, normalises each
row, and caches to results/datasets/. The `_s` / `_m` cleaned files are NEVER
read here — only the oracle file is selectable, so `_S` / `_M` cannot leak in.

Included question types: `knowledge-update`, `single-session-preference`, plus the
abstention variants (`_abs` question ids) of those included types. Excluded:
`single-session-assistant` (and everything else). Abstention questions have gold
"not enough information" and are scored on correct abstention.
"""
from __future__ import annotations

import json
import pathlib

# PINNED to a specific commit (resolved 2026-10-13) for reproducibility.
REVISION = "98d7416c24c778c2fee6e6f3006e7a073259d48f"
DATASET = "xiaowu0162/longmemeval-cleaned"
ORACLE_FILE = "longmemeval_oracle.json"          # the ONLY file this adapter reads
FORBIDDEN_FILES = ("longmemeval_s_cleaned.json", "longmemeval_m_cleaned.json")  # _S/_M: never selectable
CACHE = pathlib.Path(__file__).resolve().parent.parent / "results" / "datasets" / "longmemeval_oracle_selected.json"

INCLUDED_TYPES = ("knowledge-update", "single-session-preference")
EXCLUDED_TYPES = ("single-session-assistant",)
_ABS_PREFIX = "The information provided is not enough"


def _is_abstention(row: dict) -> bool:
    return str(row.get("question_id", "")).endswith("_abs") or \
        str(row.get("answer", "")).strip().startswith(_ABS_PREFIX)


def _download_oracle() -> list[dict]:
    from huggingface_hub import hf_hub_download
    path = hf_hub_download(DATASET, ORACLE_FILE, repo_type="dataset", revision=REVISION)
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))


def _normalise(row: dict) -> dict:
    """One normalised question: sessions in chronological order with their dates,
    each turn {role, content}. Haystack dates pair with haystack_sessions by index."""
    sessions_raw = row.get("haystack_sessions") or []
    dates = row.get("haystack_dates") or [None] * len(sessions_raw)
    sessions = []
    for i, sess in enumerate(sessions_raw):
        turns = [{"role": t.get("role", "user"), "content": t.get("content", "")}
                 for t in (sess or []) if isinstance(t, dict)]
        sessions.append({"date": dates[i] if i < len(dates) else None, "turns": turns})
    # chronological by date string (ISO-ish 'YYYY/MM/DD (Day) HH:MM' sorts correctly)
    sessions.sort(key=lambda s: s.get("date") or "")
    return {
        "question_id": row.get("question_id"),
        "question_type": row.get("question_type"),
        "question": row.get("question"),
        "answer": row.get("answer"),
        "question_date": row.get("question_date"),
        "is_abstention": _is_abstention(row),
        "sessions": sessions,
    }


def load(types=INCLUDED_TYPES, force: bool = False) -> dict:
    """Return {count, revision, by_type:{...}, questions:[normalised]}. Cached."""
    if CACHE.exists() and not force:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    raw = _download_oracle()
    types = set(types)
    selected = []
    for r in raw:
        qt = r.get("question_type")
        if qt in EXCLUDED_TYPES:
            continue
        if qt in types:                 # includes _abs of an included type (same qt)
            selected.append(_normalise(r))
    by_type: dict[str, int] = {}
    for q in selected:
        by_type[q["question_type"]] = by_type.get(q["question_type"], 0) + 1
    out = {"count": len(selected), "revision": REVISION, "dataset": DATASET,
           "by_type": by_type, "n_abstention": sum(1 for q in selected if q["is_abstention"]),
           "questions": selected}
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(out), encoding="utf-8")
    return out


def verify() -> tuple[bool, str]:
    try:
        d = load()
    except Exception as e:  # noqa: BLE001
        return False, f"download/load failed: {type(e).__name__}: {str(e)[:160]}"
    ku = d["by_type"].get("knowledge-update", 0)
    ssp = d["by_type"].get("single-session-preference", 0)
    ok = ku == 78 and ssp == 30
    return ok, (f"revision {REVISION[:12]} | total {d['count']} "
                f"(knowledge-update {ku}/78, single-session-preference {ssp}/30, "
                f"abstention {d['n_abstention']})")


if __name__ == "__main__":
    ok, msg = verify()
    print(("OK  " if ok else "WARN ") + msg)
