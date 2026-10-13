"""STEP 2 — "oracle mock": a calibrated, deterministic LLM provider for offline
dry-runs (LLM_PROVIDER=oracle_mock). It drives the WHOLE pipeline — salience,
frame, fill (extraction JSON), Q-A resolution, chat replies, and the evaluation
judge — with NO network.

Miscalibration is known by construction: for each expected assertion the oracle
is correct with probability `p_correct` and reports confidence `reported_conf`
(or, for a "perfect" profile, reports its own correctness probability). So the
calibration study can be validated — measured ECE must match |reported - p|.

The experiment runner sets per-turn context via set_turn()/set_qa(); token counts
are a deterministic function of input+output length so cost accounting is testable.
"""
from __future__ import annotations

import hashlib
import json
import random
import re
from typing import Any

from app import ontology

_TASK_RE = re.compile(r"\[\[TASK:([A-Z_]+)\]\]")

# Named calibration profiles. p_correct: fraction of assertions that match the
# truth table. reported_conf: the confidence the extractor reports. perfect:
# report = actual correctness (ECE ~ 0).
PROFILES = {
    "overconfident": {"p_correct": 0.70, "reported_conf": 0.90},
    "underconfident": {"p_correct": 0.90, "reported_conf": 0.60},
    "perfect": {"p_correct": 0.80, "perfect": True},
}

_STATE: dict[str, Any] = {
    "profile": PROFILES["overconfident"],
    "expected": [],      # list of assertion dicts for the current user turn
    "qa_answer": None,    # expected answer string for the current QA question
    "seed": 2026,
}


def configure(profile_name: str = "overconfident", seed: int = 2026) -> None:
    _STATE["profile"] = PROFILES.get(profile_name, PROFILES["overconfident"])
    _STATE["seed"] = seed


def set_turn(expected: list[dict]) -> None:
    """Assertions the current user message is meant to yield (empty = non-fact)."""
    _STATE["expected"] = expected or []


def set_qa(answer: str | None) -> None:
    """The gold answer for the current QA question (drives chat + judge)."""
    _STATE["qa_answer"] = answer


def _rng(*parts: Any) -> random.Random:
    h = hashlib.sha1(("|".join(str(p) for p in parts) + f"|{_STATE['seed']}").encode()).hexdigest()
    return random.Random(int(h[:12], 16))


def _tokens(system: str, user: str, out: str) -> tuple[int, int]:
    # Deterministic: 1 token ~= 1 whitespace word. Stable across runs.
    return len((system + " " + user).split()), max(1, len(out.split()))


def _wrong_value(value: str, rng: random.Random) -> str:
    pool = ["other", "unknown", "something else", "placeholder", "misc"]
    alt = rng.choice(pool)
    return alt if alt != value else alt + "2"


def _decide(assertion: dict, resample_i: int = 0) -> tuple[dict, float, bool]:
    """Return (assertion_out, reported_confidence, is_correct). Deterministic per
    (value, resample index) so self-consistency resamples are reproducible."""
    prof = _STATE["profile"]
    rng = _rng("decide", assertion.get("dimension"), assertion.get("key"),
               assertion.get("value"), resample_i)
    correct = rng.random() < prof["p_correct"]
    out = dict(assertion)
    if not correct:
        out["value"] = _wrong_value(str(assertion.get("value", "")), rng)
    conf = prof["p_correct"] if prof.get("perfect") else prof["reported_conf"]
    out["confidence"] = round(conf, 4)
    out.setdefault("subject", "self")
    out.setdefault("operation", assertion.get("operation", "ADD"))
    out.setdefault("explicitness", assertion.get("explicitness", "explicit"))
    out.setdefault("quote", str(assertion.get("value", "")))
    out.setdefault("attributes", assertion.get("attributes", []))
    return out, conf, correct


def _resample_index(user: str) -> int:
    # A FILL resample (self-consistency) repeats the same TARGET message; count
    # repeats within a short window via a per-message call counter.
    key = hashlib.sha1(user.encode()).hexdigest()[:12]
    _STATE.setdefault("_calls", {})
    _STATE["_calls"][key] = _STATE["_calls"].get(key, -1) + 1
    return _STATE["_calls"][key]


def oracle_complete(messages: list[dict]) -> dict[str, Any]:
    system = " ".join(m["content"] for m in messages if m["role"] == "system")
    user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
    task = (_TASK_RE.search(system) or [None, None])[1] if _TASK_RE.search(system) else None

    # Evaluation judge (runner's grader; no TASK tag). PASS iff the reply conveys
    # the gold answer, or (for abstention) correctly says it does not know.
    if task is None and "grade a chatbot" in system.lower():
        text = _judge(user)
    elif task == "SALIENCE":
        text = json.dumps({"score": 85 if _STATE["expected"] else 10,
                           "reason": "oracle"})
    elif task == "FRAME":
        frames = [{"subject": a.get("subject", "self"), "dimension": a["dimension"],
                   "operation": a.get("operation", "ADD")} for a in _STATE["expected"]]
        text = json.dumps({"frames": frames})
    elif task == "FILL":
        ri = _resample_index(user)
        tm = re.search(r"TARGET message:\s*(.+)", user)
        target_msg = tm.group(1).strip() if tm else user
        asserts = []
        for a in _STATE["expected"]:
            out = _decide(a, ri)[0]
            out["quote"] = target_msg  # ground the quote in the real user message
            asserts.append(out)
        text = json.dumps({"assertions": asserts, "relations": []})
    elif task == "QA_RESOLVE":
        text = user.split("User's reply:")[-1].strip() or "UNRESOLVED"
    elif task == "QA_ANSWER":
        text = _qa_answer(system, user)
    else:  # CHAT / read path
        text = _chat_reply(system, user)

    pt, ct = _tokens(system, user, text)
    return {"text": text, "prompt_tokens": pt, "completion_tokens": ct, "latency_ms": 1}


def _chat_reply(system: str, user: str) -> str:
    """Read path: answer a question from the gold answer (if set) or the state
    block; otherwise a short ack. Deterministic — good enough for the judge."""
    ans = _STATE.get("qa_answer")
    q = "?" in user or bool(re.match(r"\s*(what|where|who|how|when|which|do|does|is|are|am)\b", user, re.I))
    if q:
        if ans in (None, "", "__unknown__"):
            return "I don't have that in what you've told me, so I don't know."
        return f"Based on what you've told me, {ans}."
    return "Got it — noted."


def _qa_answer(system: str, user: str) -> str:
    """QA answerer for the four-system comparison. Deterministic, context-driven:
    a system answers correctly only if the gold (set via set_qa) is actually in
    the CONTEXT it was given — so no_memory (no context) abstains, full_context
    (all turns) usually has it, vector_rag/PCSG depend on retrieval/graph. When
    the gold IS present, correctness still follows the profile's p_correct and the
    reported confidence follows reported_conf, so answer-level ECE is miscalibrated
    by construction. Returns JSON {answer, confidence}."""
    prof = _STATE["profile"]
    gold = _STATE.get("qa_answer")
    ctx = system.split("CONTEXT:", 1)[-1].lower() if "CONTEXT:" in system else ""
    # unanswerable question: the calibrated action is to abstain.
    if gold in (None, "", "__unknown__"):
        return json.dumps({"answer": "I don't know", "confidence": round(1 - prof.get("reported_conf", prof["p_correct"]), 4)})
    key = ontology.normalize_value(str(gold)).split()
    key0 = key[0] if key else ""
    if not key0 or key0 not in ctx:
        return json.dumps({"answer": "I don't know", "confidence": 0.2})
    rng = _rng("qa_answer", gold, ctx[:40])
    correct = rng.random() < prof["p_correct"]
    conf = prof["p_correct"] if prof.get("perfect") else prof["reported_conf"]
    ans = str(gold) if correct else _wrong_value(str(gold), rng)
    return json.dumps({"answer": ans, "confidence": round(conf, 4)})


def _judge(user: str) -> str:
    """user payload is 'RUBRIC: ... REPLY: ...'. In dry-run the gold answer in
    _STATE drives a deterministic verdict."""
    reply = user.split("REPLY:")[-1].strip().lower()
    ans = _STATE.get("qa_answer")
    if ans in (None, "", "__unknown__"):
        ok = any(p in reply for p in ("don't know", "do not know", "no record", "not sure", "don't have"))
        return f"{'PASS' if ok else 'FAIL'}: abstention check"
    ok = ontology.normalize_value(str(ans)).split()[0] in reply if ans else False
    return f"{'PASS' if ok else 'FAIL'}: answer check"
