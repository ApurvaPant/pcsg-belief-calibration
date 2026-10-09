"""LongMemEval knowledge-update adapter (STEP 5).

Downloads xiaowu0162/longmemeval-cleaned at a PINNED revision (no paid API cost),
extracts the knowledge-update questions with their history sessions and gold
answers, verifies the count is 78, and caches to results/datasets/. The experiment
read path answers from these histories and the step-1 judge grades them.
"""
from __future__ import annotations

import json
import pathlib

# Pinned revision — update once, then frozen for reproducibility.
REVISION = "main"
DATASET = "xiaowu0162/longmemeval-cleaned"
CACHE = pathlib.Path(__file__).resolve().parent.parent / "results" / "datasets" / "longmemeval_ku.json"
EXPECTED_KU = 78


def _is_knowledge_update(row: dict) -> bool:
    qt = str(row.get("question_type", "")).lower()
    return "knowledge" in qt and "update" in qt or qt in ("knowledge-update", "knowledge_update")


def _iter_raw_records():
    """Download the dataset's raw JSON/JSONL files directly via huggingface_hub
    (the cleaned repo is not a standard `datasets` loader), yielding dict rows."""
    from huggingface_hub import hf_hub_download, list_repo_files
    files = [f for f in list_repo_files(DATASET, repo_type="dataset", revision=REVISION)
             if f.endswith((".json", ".jsonl"))]
    for f in files:
        path = hf_hub_download(DATASET, f, repo_type="dataset", revision=REVISION)
        text = open(path, encoding="utf-8").read()
        try:
            data = json.loads(text)
            rows = data if isinstance(data, list) else data.get("data", data.get("questions", []))
        except json.JSONDecodeError:
            rows = [json.loads(ln) for ln in text.splitlines() if ln.strip()]
        for r in (rows if isinstance(rows, list) else []):
            if isinstance(r, dict):
                yield r


def build(force: bool = False) -> dict:
    """Return {count, revision, questions:[{id,question,gold,history}]}, cached."""
    if CACHE.exists() and not force:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    rows = []
    for r in _iter_raw_records():
        if _is_knowledge_update(r):
            rows.append({
                "id": r.get("question_id") or r.get("id") or f"ku_{len(rows)}",
                "question": r.get("question"),
                "gold": r.get("answer") or r.get("gold_answer"),
                "history": r.get("haystack_sessions") or r.get("history") or r.get("sessions"),
            })
    out = {"count": len(rows), "revision": REVISION, "dataset": DATASET, "questions": rows}
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(out)[:50_000_000], encoding="utf-8")
    return out


def verify() -> tuple[bool, str]:
    try:
        d = build()
    except Exception as e:  # noqa: BLE001
        return False, f"download/build failed: {type(e).__name__}: {str(e)[:160]}"
    ok = d["count"] == EXPECTED_KU
    return ok, f"knowledge-update questions: {d['count']} (expected {EXPECTED_KU})"


if __name__ == "__main__":
    ok, msg = verify()
    print(("OK  " if ok else "WARN ") + msg)
