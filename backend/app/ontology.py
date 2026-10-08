"""PCSG slot ontology.

Defines the slot space every assertion must map to. A slot is a
`(dimension, key)` pair, and its **cardinality** decides reconciliation:

  * SINGLE — the slot holds exactly one active value; a new value supersedes
    the old one (e.g. identity/name, preference/diet).
  * MULTI  — the slot holds many active values simultaneously; a new value is
    a NEW node and only supersedes when explicitly told to (e.g. habit/jogging
    coexists with habit/reading).

Keys are always snake_case and derived from content. There is no `general`
fallback: if a key cannot be determined the assertion is dropped upstream.
"""
from __future__ import annotations

import re

# ── Cardinality ─────────────────────────────────────────────────────────── #
SINGLE = "SINGLE"
MULTI = "MULTI"

# ── Node lifecycle ──────────────────────────────────────────────────────── #
STATUS_ACTIVE = "active"
STATUS_SUPERSEDED = "superseded"
STATUS_DELETED = "deleted"

# status_detail: finer-grained lifecycle for MULTI slots that can end.
DETAIL_ACTIVE = "active"
DETAIL_ENDED = "ended"        # a habit stopped, a subscription cancelled
DETAIL_ACHIEVED = "achieved"  # a goal reached
DETAIL_ABANDONED = "abandoned"  # a project/goal given up
STATUS_DETAILS = (DETAIL_ACTIVE, DETAIL_ENDED, DETAIL_ACHIEVED, DETAIL_ABANDONED)

# ── The slot table ──────────────────────────────────────────────────────── #
# dimension -> default cardinality (applies to any key not overridden below).
# For open-key dimensions (skill, project, habit, ...) the key IS the content,
# so every key in that dimension shares the dimension's cardinality.
DIMENSION_DEFAULT_CARDINALITY: dict[str, str] = {
    "identity": SINGLE,
    "preference": MULTI,   # mixed — see KEY_CARDINALITY; unknown keys coexist
    "interest": MULTI,
    "goal": MULTI,
    "skill": MULTI,
    "project": MULTI,
    "habit": MULTI,
    "belief": MULTI,
    "relationship": MULTI,
    "communication_style": SINGLE,
}

DIMENSIONS: tuple[str, ...] = tuple(DIMENSION_DEFAULT_CARDINALITY.keys())

# Per-slot overrides — only needed where a dimension is mixed-cardinality.
KEY_CARDINALITY: dict[tuple[str, str], str] = {
    ("preference", "diet"): SINGLE,
    ("preference", "communication_tone"): SINGLE,
    ("preference", "response_length"): SINGLE,
    ("preference", "work_style"): SINGLE,
    ("preference", "food_likes"): MULTI,
    ("preference", "food_dislikes"): MULTI,
    ("preference", "music_genre"): MULTI,
    ("preference", "tool_choice"): MULTI,
}

# Enumerated keys for closed-key dimensions — surfaced to the extractor so it
# picks a canonical slot rather than inventing one. Open-key dimensions map to
# None (the key is derived from content, e.g. the skill or habit itself).
KNOWN_KEYS: dict[str, list[str] | None] = {
    "identity": ["name", "age_range", "location", "occupation", "employer"],
    "preference": [
        "diet", "communication_tone", "response_length", "work_style",
        "food_likes", "food_dislikes", "music_genre", "tool_choice",
    ],
    "interest": ["topic"],
    "goal": ["career", "learning", "fitness", "financial", "personal"],
    "skill": None,
    "project": None,
    "habit": None,
    "belief": None,
    "relationship": None,
    "communication_style": ["formality", "verbosity", "humour"],
}


def cardinality(dimension: str, key: str) -> str:
    """Return SINGLE or MULTI for a slot. Defaults to MULTI when unknown so an
    unrecognised value coexists rather than clobbering an existing one."""
    if (dimension, key) in KEY_CARDINALITY:
        return KEY_CARDINALITY[(dimension, key)]
    return DIMENSION_DEFAULT_CARDINALITY.get(dimension, MULTI)


def is_single(dimension: str, key: str) -> bool:
    return cardinality(dimension, key) == SINGLE


def is_valid_dimension(dimension: str) -> bool:
    return dimension in DIMENSION_DEFAULT_CARDINALITY


# ── Normalisation ───────────────────────────────────────────────────────── #
# Leading words stripped from a value: articles, copulas, and dangling
# infinitive/gerund lead-ins. Only stripped from the FRONT, greedily.
_LEADING = {
    "a", "an", "the", "to", "is", "are", "am", "was", "were", "be", "been",
    "being", "do", "does", "did", "doing", "my", "of", "that", "it", "i",
    "want", "wants", "wanting", "like", "likes", "liking", "love", "loves",
    "loving", "enjoy", "enjoys", "enjoying", "prefer", "prefers", "preferring",
}
_WORD = re.compile(r"[a-z0-9]+(?:['-][a-z0-9]+)*")


def normalize_value(text: str) -> str:
    """Lowercase, strip leading verbs/articles and trailing punctuation.

    "is to become a pilot" -> "become a pilot"
    "doing machine learning" -> "machine learning"
    """
    if not text:
        return ""
    s = text.strip().lower().strip("\"'")
    s = re.sub(r"[\s]+", " ", s)
    words = s.split(" ")
    i = 0
    while i < len(words) - 1 and re.sub(r"[^a-z0-9'-]", "", words[i]) in _LEADING:
        i += 1
    s = " ".join(words[i:])
    return s.strip(" .,:;!?-").strip()


def normalize_key(text: str | None) -> str | None:
    """snake_case a key, or None if it cannot be formed."""
    if not text:
        return None
    tokens = _WORD.findall(text.lower())
    if not tokens:
        return None
    return "_".join(tokens)


# ── Node attributes (qualifiers) ────────────────────────────────────────── #
# Allowed attribute names per dimension. Anything outside the set is rejected
# rather than stored as a free-form key. `preference` also permits open-ended
# type-specific qualifiers (e.g. fermentation_duration on a wine).
ATTRIBUTES_BY_DIMENSION: dict[str, set[str]] = {
    "relationship": {"duration", "since", "relation_type", "status",
                     "name", "occupation", "birthday", "age", "location"},
    "skill": {"proficiency", "years_experience", "learning_status"},
    "habit": {"frequency", "duration", "time_of_day", "status",
              "genre", "topic", "detail"},  # refinement answers qualify the habit
    "goal": {"target_date", "progress", "priority", "status"},
    "project": {"role", "status", "started", "tech_stack"},
    "interest": {"intensity", "since"},
    "preference": {"strength", "context", "since"},  # + type-specific (open)
    "identity": set(),               # none — the value is the whole assertion
    "belief": set(),
    "communication_style": set(),
}


def allowed_attribute(dimension: str, name: str) -> bool:
    if dimension not in ATTRIBUTES_BY_DIMENSION:
        return False
    base = ATTRIBUTES_BY_DIMENSION[dimension]
    if name in base:
        return True
    # preference carries open-ended type-specific qualifiers.
    return dimension == "preference" and bool(_WORD.findall(name))


# ── Value validation (PART 2) ───────────────────────────────────────────── #
# A node value must be a short noun phrase — never a sentence.
PREPOSITIONS = {"with", "over", "from", "for", "about", "that"}
_MAX_VALUE_WORDS = 4


def is_valid_value(value: str) -> bool:
    words = value.split()
    if not words or len(words) > _MAX_VALUE_WORDS:
        return False
    return not any(w in PREPOSITIONS for w in words)


# ── Inter-node relations (PART 8) ───────────────────────────────────────── #
RELATION_TYPES = {"part_of", "requires", "blocks", "related_to", "works_on",
                  "involves", "gets_along_with"}


def is_valid_relation(relation: str) -> bool:
    return relation in RELATION_TYPES


# ── Cross-dimension duplicate policy (fix step 4) ───────────────────────────#
# A value may legitimately exist under two dimensions only when the two mean
# genuinely different things. The canonical case is a language that is both a
# PREFERENCE (the user likes it) and a SKILL (the user can use it): e.g.
# skill/german vs preference/german. Any other same-value cross-dimension pair
# is a duplicate and should REFINE the existing node rather than create a new
# one (logged as dimension_conflict).
_COEXIST_PAIRS: frozenset[frozenset[str]] = frozenset({frozenset({"preference", "skill"})})


def may_coexist_cross_dim(dim_a: str, dim_b: str) -> bool:
    """True when the same value is allowed to live under both dimensions."""
    return dim_a != dim_b and frozenset({dim_a, dim_b}) in _COEXIST_PAIRS


# How the user wants responses written is ALWAYS communication_style, never
# interest. Injected into the structuring prompt's ontology card (fix step 4).
COMM_STYLE_RULE = (
    "How the user wants responses WRITTEN (length, tone, format, verbosity, "
    "formality, e.g. 'concise answers', 'no emojis', 'explain simply') is ALWAYS "
    "dimension communication_style, NEVER interest or preference."
)


# ── Key format + identity self-reference guard (PART 2/6) ───────────────── #
_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def valid_key_format(key: str) -> bool:
    return bool(key) and key != "general" and bool(_KEY_RE.match(key))


# A direct first-person self-reference ("my name is X", "i'm called X", "i am X").
_SELF_ID_RE = re.compile(r"\b(my name is|i'?m called|call me|i am|i'?m)\s+\w+", re.I)
# A possessive noun between the pronoun and the attribute ("my dog name is ...").
_POSSESSIVE_NOUN_RE = re.compile(r"\bmy\s+\w+\s+name\s+is\b", re.I)


def is_self_identity_quote(quote: str) -> bool:
    """True only for a direct self-identification, never 'my <noun> name is X'."""
    q = quote or ""
    if _POSSESSIVE_NOUN_RE.search(q):
        return False
    return bool(_SELF_ID_RE.search(q))


def strip_key_from_value(key: str, value: str) -> str:
    """Drop a leading/trailing relation word that duplicates the key
    (key=girlfriend, value='girlfriend hailey' -> 'hailey')."""
    key_toks = set(key.split("_"))
    toks = value.split()
    while toks and toks[0] in key_toks:
        toks.pop(0)
    while toks and toks[-1] in key_toks:
        toks.pop()
    return " ".join(toks) or value


def decompose_value(value: str) -> str | None:
    """Best-effort fallback: the head phrase before the first preposition.
    Primary decomposition (value + attributes) is the extractor's job; this
    only salvages an over-long value so we store a phrase, not a sentence."""
    words = value.split()
    head: list[str] = []
    for w in words:
        if w in PREPOSITIONS:
            break
        head.append(w)
    phrase = " ".join(head).strip(" .,:;!?-")
    return phrase or None
