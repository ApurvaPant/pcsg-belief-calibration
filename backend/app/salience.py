"""Tier-0 salience gate — a cheap, deterministic ROUTER (not a veto).

It errs toward letting turns through: extraction (tier-2) is the precision
stage, so the gate only needs near-zero false negatives. It rejects three
things — pure pleasantries, questions addressed to the assistant, and messages
under four tokens — and passes everything else, with high salience for any
message carrying a strong self-state signal.

No network call: rules run locally, so gating is free and provider-independent.
"""
from __future__ import annotations

import re

from . import llm
from .config import settings
from .utils import parse_json

_TOKEN = re.compile(r"[a-z0-9']+", re.I)

# ── Strong signals — ALWAYS pass, regardless of length ──────────────────── #
_STRONG = [
    ("self_identification",
     re.compile(r"\b(my name is|i'?m called|call me|name'?s|i am an? |i'?m an? )", re.I)),
    ("cessation_negation",
     re.compile(r"\b(stopped|quit|quitting|no longer|not anymore|gave up|give up|"
                r"giving up|used to|don'?t\b.*\banymore)\b", re.I)),
    ("explicit_replacement",
     re.compile(r"\b(switched to|switched from|moved from|moved to|instead of|"
                r"changed to|changed from)\b", re.I)),
]

# ── Medium signals — pass (durable self-state likely) ───────────────────── #
_MEDIUM = [
    ("preference_verb",
     re.compile(r"\b(i (really |absolutely |generally )?(like|love|prefer|enjoy|"
                r"hate|dislike|adore|favou?rite)|my favou?rite)\b", re.I)),
    ("temporal_marker",
     re.compile(r"\b(usually|always|every (day|morning|night|week)|these days|"
                r"lately|nowadays|recently|now|anymore)\b", re.I)),
    ("first_person_state",
     re.compile(r"\b(i am|i'?m|i have|i'?ve|i work|i live|i want|i wanna|i aim|"
                r"i plan|i hope|i'?m learning|i study|i believe|i think|i feel|"
                r"i can|i know|i own|i use|i play|my \w+ is)\b", re.I)),
]

# ── Reject: pure pleasantries (message is nothing but filler) ───────────── #
_FILLER = {
    "thanks", "thank", "thankyou", "thx", "ty", "ok", "okay", "k", "kk",
    "cool", "nice", "great", "awesome", "sweet", "lol", "lmao", "haha",
    "hehe", "hi", "hello", "hey", "yo", "sup", "bye", "goodbye", "yes",
    "yeah", "yep", "no", "nope", "sure", "fine", "good", "morning",
    "evening", "night", "please", "welcome", "np", "gotcha", "got", "it",
    "you", "man", "cheers", "right", "indeed", "wow",
}

_QUESTION_START = re.compile(
    r"^\s*(what|how|why|when|who|where|which|can|could|would|will|should|do|does|"
    r"did|is|are|am|was|were|has|have|shall|may)\b", re.I)
_FIRST_PERSON = re.compile(r"\b(i|i'?m|i'?ve|my|me|mine|myself)\b", re.I)
_ADDRESSES_ASSISTANT = re.compile(r"\b(you|your|you'?re|u)\b", re.I)


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text)


def _is_pleasantry(tokens: list[str]) -> bool:
    return bool(tokens) and all(t.lower() in _FILLER for t in tokens)


def _is_question_to_assistant(text: str, tokens: list[str]) -> bool:
    is_q = text.strip().endswith("?") or bool(_QUESTION_START.match(text))
    if not is_q:
        return False
    # A question directed at the assistant ("can you help me?") is rejected even
    # if it says "me". A question that reveals self-state without addressing the
    # assistant ("why am i so tired?") is worth keeping.
    if _ADDRESSES_ASSISTANT.search(text):
        return True
    return not _FIRST_PERSON.search(text)


def _rule_score(message: str) -> tuple[float, str]:
    """Deterministic Tier-0 routing score. Never hits the network."""
    text = message.strip()
    tokens = _tokens(text)

    for reason, pattern in _STRONG:
        if pattern.search(text):
            return 0.9, reason

    # Transient mood / non-statements are not durable facts (bug 4). Checked
    # before the medium first-person rule so "i am doing great" scores low.
    if _TRANSIENT.search(text) or _NON_STATEMENT.search(text):
        return 0.25, "transient / non-statement"

    for reason, pattern in _MEDIUM:
        if pattern.search(text):
            return 0.75, reason

    if _is_pleasantry(tokens):
        return 0.12, "pleasantry"
    if _is_question_to_assistant(text, tokens):
        return 0.2, "question to assistant"
    if len(tokens) < 4:
        return 0.15, "too short"

    if _FIRST_PERSON.search(text):
        return 0.65, "first-person statement"
    return 0.55, "routed (no strong signal)"


# Transient states — true right now, not durable.
_TRANSIENT = re.compile(
    r"\b(i'?m|i am|feeling|feel)\s+(doing\s+)?(great|good|fine|ok|okay|well|bad|"
    r"tired|happy|sad|bored|busy|hungry|sleepy|excited|stressed|sick)\b", re.I)
# Non-statements / filler with no assertable content.
_NON_STATEMENT = re.compile(
    r"\b(no nothing|nothing (such|much)|as of now|not really|i guess|dunno|idk)\b", re.I)


_CLASSIFIER_SYSTEM = (
    "[[TASK:SALIENCE]] You score how DURABLE a user's message is for a system "
    "that remembers facts about the user. Ask: would this still be useful to "
    "know in a week?\n"
    "HIGH (70-100): stated facts about the user, changes, corrections, "
    "preferences, plans, goals, relationships, or an answer to a question the "
    "assistant just asked.\n"
    "LOW (0-30): transient mood ('i am doing great'), non-statements, one-off "
    "actions with no lasting implication ('i drank a beer'), pleasantries, "
    "questions to the assistant, meta-instructions.\n"
    "Output an INTEGER 0-100 (spread the range; do not snap to round numbers). "
    'Respond ONLY with JSON: {"score": <integer 0-100>}'
)


def _llm_classify(message: str) -> tuple[float, int, str]:
    """Cheap classifier salience (real providers only). Returns (float, raw_int, model)."""
    model = llm.resolve_model("classifier")
    result = llm.complete(
        messages=[
            {"role": "system", "content": _CLASSIFIER_SYSTEM},
            {"role": "user", "content": message},
        ],
        model=model,
        temperature=0.0,
        max_tokens=20,
    )
    parsed = parse_json(result["text"])
    raw = 0
    if isinstance(parsed, dict):
        try:
            raw = int(round(float(parsed.get("score", 0))))
        except (TypeError, ValueError):
            raw = 0
    raw = max(0, min(100, raw))
    return raw / 100.0, raw, model


def score_salience(message: str) -> tuple[float, str, str | None, int]:
    """Return (score, reason, classifier_model, raw_int).

    Tier-0 rules run first as a RECALL FLOOR (strong self-state signals always
    pass). On a real provider the cheap classifier ALSO scores the turn as an
    integer 0-100; we take the max so it can upgrade borderline turns but never
    veto what the rules recognised. The mock uses rules only (no network).
    """
    rscore, rreason = _rule_score(message)
    if settings.llm.provider == "mock":
        return rscore, rreason, "mock", int(round(rscore * 100))
    try:
        cscore, craw, cmodel = _llm_classify(message)
    except Exception:  # noqa: BLE001 — classifier down: rely on the rule floor
        return rscore, rreason + "; classifier_error", None, int(round(rscore * 100))
    return max(rscore, cscore), f"{rreason}; classifier={craw}", cmodel, craw


def is_salient(score: float) -> bool:
    return score >= settings.pipeline.salience_threshold


# ── Bug 1: interrogatives must never produce assertions ─────────────────── #
_WH = r"(?:what'?s?|who'?s?|whom|whose|which|where'?s?|when'?s?|why|how)"
_AUX = (r"(?:do|does|did|is|are|am|was|were|can|could|would|will|shall|should|"
        r"have|has|had|may|might|don'?t|doesn'?t|didn'?t)")
_WH_START = re.compile(rf"^\s*{_WH}\b", re.I)
_AUX_START = re.compile(rf"^\s*{_AUX}\b\s+(?:i|my|you|your|we|it|he|she|they|the|a|an|there)\b", re.I)
_DECL_MARK = re.compile(r"\b(i|i'?m|im|my|me|we|our|mine)\b", re.I)
_SPLIT = re.compile(r"[?;,]|\band\b|\bbut\b", re.I)


def _is_interrogative(clause: str) -> bool:
    c = clause.strip()
    if not c:
        return False
    return c.endswith("?") or bool(_WH_START.match(c)) or bool(_AUX_START.match(c))


# ── Tier-0 deterministic END detection (closed vocabulary) ─────────────── #
# A cessation is a real stop/leave/quit verb — or a negation that is explicitly
# scoped by "anymore". Plain "not" / "don't" / "doesn't" / "never" are NOT
# cessation: they assert a negated fact, not the end of something (critical bug).
CESSATION_RE = re.compile(
    r"\b(stopped|quit|quitting|gave up on|gave up|broke up with|split up with|split up|"
    r"left|moved out of|moved away from|dropped out|graduated from|sold|cancell?ed|"
    r"no longer|used to)\b"
    r"|\b(?:not|never|don'?t|doesn'?t|didn'?t|won'?t|isn'?t|aren'?t)\b[^.?!]*\banymore\b",
    re.I)
_END_CUES = CESSATION_RE
_END_LEAD = re.compile(r"^\s*(?:doing|the|my|a|an|with)\s+", re.I)


def has_cessation(text: str) -> bool:
    """True if the text carries an explicit cessation verb (used by the
    destructive-operation guard). Plain negation alone returns False."""
    return bool(CESSATION_RE.search(text or ""))


def detect_end(text: str) -> tuple[bool, str | None]:
    """Rule-matched cessation. Returns (is_end, object_phrase). A match here is
    a CONSTRAINT the extractor cannot override, not a suggestion."""
    m = _END_CUES.search(text)
    if not m:
        return False, None
    obj = text[m.end():].strip(" .,:;!?-")
    while _END_LEAD.match(obj):
        obj = _END_LEAD.sub("", obj, count=1).strip()
    return True, (obj or None)


def classify_message(text: str) -> tuple[bool, str]:
    """Return (is_pure_query, extract_text).

    A pure interrogative (no declarative clause) is a retrieval query: it must
    skip extraction entirely. A mixed message ("my name is X, what's yours?")
    yields only its declarative clauses for extraction.
    """
    if not _is_interrogative(text):
        return False, text
    decl = [
        s.strip(" .!,")
        for s in _SPLIT.split(text)
        if s.strip() and not _is_interrogative(s) and _DECL_MARK.search(s)
    ]
    if decl:
        return False, ". ".join(decl)
    return True, ""
