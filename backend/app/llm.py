"""The ONLY module permitted to make network calls to a model.

Exposes a provider-agnostic surface used by the rest of the app:

    complete(messages, model, temperature, max_tokens)
        -> {text, prompt_tokens, completion_tokens, latency_ms}
    embed(texts, model) -> list[list[float]]

Real calls go to any OpenAI-compatible HTTP endpoint via httpx (no vendor
SDK). Setting LLM_PROVIDER=mock selects a deterministic, offline provider so
the whole pipeline and the test-suite run with no API key and no cost.

Task routing for the mock is driven by a `[[TASK:...]]` tag that the prompt
builders embed in the system message. Real providers treat the tag as inert
text, so the two code paths stay identical from the caller's point of view.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
from typing import Any

import httpx

from . import ontology
from .config import settings

Message = dict[str, str]  # {"role": "system"|"user"|"assistant", "content": str}

# Fallback model ids if config.yaml is missing a role (real providers only).
_DEFAULT_MODELS = {
    "chat": "openai/gpt-4o-mini",
    "classifier": "openai/gpt-4o-mini",
    "extractor": "openai/gpt-4o-mini",
    "embedding": "openai/text-embedding-3-small",
}


def resolve_model(role: str) -> str:
    """The model string that WILL handle a call for `role`.

    Returns "mock" under the mock provider so gate_log / nodes record which
    model produced a result honestly, without a network call.
    """
    if settings.llm.provider == "mock":
        return "mock"
    return settings.llm.models.get(role) or _DEFAULT_MODELS.get(role, "openai/gpt-4o-mini")


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def complete(
    messages: list[Message],
    model: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    response_schema: dict | None = None,
    schema_name: str = "output",
) -> dict[str, Any]:
    """Return a chat completion as a normalized dict.

    Keys: text, prompt_tokens, completion_tokens, latency_ms. When
    `response_schema` is given, request the provider's structured-output
    (JSON-schema) mode so the model can only emit conforming JSON (PART 6).
    """
    model = model or settings.llm.models.get("chat", "gpt-4o-mini")
    temperature = settings.llm.temperature if temperature is None else temperature
    max_tokens = settings.llm.max_tokens if max_tokens is None else max_tokens

    if settings.llm.provider == "mock":
        return _mock_complete(messages)
    return _http_complete(messages, model, temperature, max_tokens, response_schema, schema_name)


def embed(texts: list[str], model: str | None = None) -> list[list[float]]:
    """Return one embedding vector per input text.

    Embeddings are not used by the PCSG pipeline today (no vector DB). OpenRouter
    has no /embeddings endpoint, so on any failure we fall back to a deterministic
    local vector rather than pulling a heavy sentence-transformers dependency.
    """
    if settings.llm.provider == "mock":
        return [_mock_embed(t) for t in texts]
    model = model or resolve_model("embedding")
    try:
        return _http_embed(texts, model)
    except httpx.HTTPError:
        return [_mock_embed(t) for t in texts]


# --------------------------------------------------------------------------- #
# HTTP (OpenAI-compatible) provider
# --------------------------------------------------------------------------- #
def _headers() -> dict[str, str]:
    h = {"Content-Type": "application/json"}
    if settings.llm.api_key:
        h["Authorization"] = f"Bearer {settings.llm.api_key}"
    # OpenRouter attribution headers (optional, used for their dashboard).
    if settings.llm.provider == "openrouter":
        h["HTTP-Referer"] = "http://localhost:5173"
        h["X-Title"] = os.getenv("OPENROUTER_APP_TITLE", "User-State Graph Chat")
    return h


def startup_check() -> None:
    """Fail loudly (don't silently fall back to mock) when a real provider is
    configured but unusable: missing key, unreachable endpoint, or 401."""
    provider = settings.llm.provider
    if provider == "mock":
        return
    if not settings.llm.api_key:
        raise RuntimeError(
            f"LLM_PROVIDER={provider} but no API key set. Provide OPENROUTER_API_KEY "
            f"(or OPENAI_API_KEY), or set LLM_PROVIDER=mock."
        )
    model = resolve_model("classifier")
    try:
        _http_complete([{"role": "user", "content": "ping"}], model, 0.0, 1)
    except httpx.HTTPStatusError as e:
        code = e.response.status_code
        if code == 401:
            raise RuntimeError(
                f"LLM auth failed (401) for provider={provider} at {settings.llm.base_url}. "
                f"Check your API key."
            ) from e
        raise RuntimeError(
            f"LLM startup probe failed ({code}) for provider={provider} model={model}: "
            f"{e.response.text[:200]}"
        ) from e
    except httpx.HTTPError as e:
        raise RuntimeError(
            f"Cannot reach LLM endpoint {settings.llm.base_url} for provider={provider}: {e}"
        ) from e


def _http_complete(
    messages: list[Message], model: str, temperature: float, max_tokens: int,
    response_schema: dict | None = None, schema_name: str = "output",
) -> dict[str, Any]:
    url = settings.llm.base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if response_schema is not None:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": schema_name, "strict": False, "schema": response_schema},
        }
    started = time.perf_counter()
    with httpx.Client(timeout=settings.llm.timeout_seconds) as client:
        resp = client.post(url, headers=_headers(), json=payload)
        resp.raise_for_status()
        data = resp.json()
    latency_ms = int((time.perf_counter() - started) * 1000)

    text = data["choices"][0]["message"]["content"] or ""
    usage = data.get("usage", {}) or {}
    return {
        "text": text,
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "completion_tokens": usage.get("completion_tokens", 0),
        "latency_ms": latency_ms,
    }


def _http_embed(texts: list[str], model: str) -> list[list[float]]:
    url = settings.llm.base_url.rstrip("/") + "/embeddings"
    payload = {"model": model, "input": texts}
    with httpx.Client(timeout=settings.llm.timeout_seconds) as client:
        resp = client.post(url, headers=_headers(), json=payload)
        resp.raise_for_status()
        data = resp.json()
    return [item["embedding"] for item in data["data"]]


# --------------------------------------------------------------------------- #
# Mock provider — deterministic, offline
# --------------------------------------------------------------------------- #
_TASK_RE = re.compile(r"\[\[TASK:([A-Z_]+)\]\]")


def _mock_complete(messages: list[Message]) -> dict[str, Any]:
    system = " ".join(m["content"] for m in messages if m["role"] == "system")
    user = next(
        (m["content"] for m in reversed(messages) if m["role"] == "user"), ""
    )
    match = _TASK_RE.search(system)
    task = match.group(1) if match else "CHAT"

    if task == "SALIENCE":
        text = json.dumps(_mock_salience(user))
    elif task == "EXTRACTION":
        text = json.dumps({"assertions": mock_extract(user), "relations": mock_relations(user)})
    else:
        text = _mock_chat_reply(user)

    return {
        "text": text,
        "prompt_tokens": len(system.split()) + len(user.split()),
        "completion_tokens": len(text.split()),
        "latency_ms": 1,
    }


def _mock_embed(text: str, dim: int = 64) -> list[float]:
    """Deterministic bag-of-hashed-tokens embedding, L2-normalized."""
    vec = [0.0] * dim
    for tok in re.findall(r"[a-z0-9]+", text.lower()):
        h = int(hashlib.sha1(tok.encode()).hexdigest(), 16)
        vec[h % dim] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def _mock_salience(user: str) -> dict[str, Any]:
    """Score a turn's likelihood of carrying durable user-state."""
    assertions = mock_extract(user)
    score = min(0.95, 0.55 + 0.15 * len(assertions)) if assertions else 0.1
    reason = (
        f"matched {len(assertions)} candidate assertion(s)"
        if assertions
        else "no first-person state signal detected"
    )
    return {"score": round(score, 2), "reason": reason}


def _mock_chat_reply(user: str) -> str:
    facts = mock_extract(user)
    # Ask a follow-up on a new relationship so a pending slot opens (PART 6).
    for f in facts:
        if f.get("dimension") == "relationship" and f.get("operation") == "ADD":
            return f"Nice — how long have you two been together?"
        if f.get("dimension") == "habit" and f.get("operation") == "ADD":
            return "Got it — how often do you do that?"
    if not facts:
        return (
            "Got it. Tell me more about what you're working on, what you like, "
            "or what you're trying to achieve."
        )
    bits = ", ".join(f"{f['dimension']}: {f['value']}" for f in facts)
    return f"Noted — I've updated my model of you ({bits}). What else?"


# --- Deterministic rule-based extraction used by the mock ------------------- #
# Content vocabularies used to classify a phrase into a slot. These make the
# mock emit realistic (dimension, key) pairs offline so the pipeline and tests
# run with no API key. The real provider does this far better.
_FOODS = {
    "momo", "momos", "pizza", "biryani", "sushi", "pasta", "dosa", "ramen",
    "tacos", "burgers", "chocolate", "mango", "noodles", "ice cream", "paneer",
}
_ACTIVITIES = {
    "jogging", "running", "reading", "meditation", "swimming", "cycling",
    "yoga", "hiking", "painting", "gaming", "gardening", "journaling",
}
_TOOLS = {
    "python", "rust", "java", "javascript", "typescript", "go", "golang",
    "c++", "ruby", "kotlin", "swift", "vim", "vscode", "emacs",
}
_RELATION_ROLES = {
    "wife", "husband", "partner", "friend", "manager", "boss", "colleague",
    "mentor", "mom", "dad", "brother", "sister", "teammate",
}


def _classify(phrase: str) -> tuple[str, str, str] | None:
    """Map a free phrase to (dimension, key, value). None if unclassifiable.
    Uses whole-word matches (so 'momo' does not match inside 'momos') and
    prefers the longest vocabulary term."""
    lower = phrase.lower()
    for food in sorted(_FOODS, key=len, reverse=True):
        if re.search(rf"\b{re.escape(food)}\b", lower):
            return "preference", "food_likes", food
    for act in sorted(_ACTIVITIES, key=len, reverse=True):
        if re.search(rf"\b{re.escape(act)}\b", lower):
            return "habit", act, act
    for tool in sorted(_TOOLS, key=len, reverse=True):
        if re.search(rf"\b{re.escape(tool)}\b", lower):
            return "preference", "tool_choice", tool
    value = ontology.normalize_value(phrase)
    if not value:
        return None
    return "interest", "topic", value


def _assertion(dimension, key, value, *, operation, quote, replaces=None,
               polarity="affirm", conf=0.9, subject="self", attributes=None, target_node=None):
    key = ontology.normalize_key(key)
    value = ontology.normalize_value(value)
    if not key or not value:
        return None
    return {
        "dimension": dimension,
        "key": key,
        "value": value,
        "operation": operation,
        "subject": subject,
        "replaces_value": ontology.normalize_value(replaces) if replaces else None,
        "polarity": polarity,
        "explicitness": "explicit",
        "quote": quote.strip(),
        "confidence": conf,
        "attributes": attributes or [],
        "target_node": target_node,
    }


_END_RE = re.compile(
    r"\b(stopped|quit|no longer|not anymore|gave up|used to|don't .* anymore|"
    r"give up|giving up|broke up with|split up|split with|left my job|dropped out|"
    r"graduated from|moved away from|cancelled|gave up on)\b",
    re.I,
)
_BREAKUP_RE = re.compile(
    r"\b(?:broke up with|split up with|split with|no longer (?:with|dating))\s+([a-z][a-z'-]*)",
    re.I,
)
_STARTED_RE = re.compile(r"\bi (?:started|began|took up|took to|now do)\s+(.+)", re.I)
_MY_REL_RE = re.compile(
    r"\bmy (girlfriend|boyfriend|wife|husband|partner|friend|manager|boss|"
    r"colleague|mentor|mom|dad|brother|sister|teammate)\s+is\s+(?!a |an )([a-z][a-z'-]*)",
    re.I,
)
# "my brother is a doctor" -> occupation attribute on the relationship node.
_MY_REL_OCC_RE = re.compile(
    r"\bmy (girlfriend|boyfriend|wife|husband|partner|friend|manager|boss|"
    r"colleague|mentor|mom|dad|brother|sister|teammate)\s+is\s+an?\s+(.+)", re.I,
)
# "my dog name is herald" -> pet node + name attribute (NOT the user's identity).
_MY_NOUN_NAME_RE = re.compile(r"\bmy (\w+)(?:'s)? name is ([a-z][a-z'-]*)", re.I)
_PETS = {"dog", "cat", "pet", "puppy", "kitten", "bird", "fish", "hamster", "rabbit"}
_HATE_RE = re.compile(r"\bi (?:hate|dislike|can't stand|cannot stand|don't like)\s+(.+)", re.I)
_TARGET_RE = re.compile(r"TARGET message:\s*(.+?)(?:\n|$)", re.S)
_HINT_RE = re.compile(r"about the '([a-z_]+)' of (?:the node )?(.+?)[\.\n]", re.I)
_LABEL_RE = re.compile(r"([a-z_]+)/([a-z_]+)=(.+)", re.I)
_DUR_RE = re.compile(r"(\d+)\s*(year|years|month|months|week|weeks|day|days)", re.I)


def mock_relations(user: str) -> list[dict[str, Any]]:
    """Emit 'X is part of Y' style relations for the mock provider."""
    mt = _TARGET_RE.search(user)
    target = mt.group(1).strip() if mt else user
    out = []
    m = re.search(r"(.+?)\s+is part of\s+(?:my\s+)?(.+)", target, re.I)
    if m:
        frm = ontology.normalize_value(m.group(1))
        to = ontology.normalize_value(m.group(2))
        if frm and to:
            out.append({"from_value": frm, "relation": "part_of", "to_value": to})
    return out
_SWAP_RE = re.compile(
    r"\b(?:switched|moved|changed)\s+from\s+(.+?)\s+to\s+(.+)", re.I
)
_SWAP_TO_RE = re.compile(r"\b(?:switched|moved|changed)\s+to\s+(.+)", re.I)
_NAME_RE = re.compile(r"\b(?:my name is|i'm called|i am called|call me|name's)\s+([a-z][a-z'-]*)", re.I)
_LOVE_RE = re.compile(r"\bi (?:really |absolutely |generally )?(?:love|like|enjoy|prefer|adore|am into|'m into)\s+(.+)", re.I)
_GOAL_RE = re.compile(r"\b(?:my goal is|i want|i wanna|i aim|i plan|i hope|i'd like|i would like)\s+(?:to\s+)?(.+)", re.I)
_SKILL_RE = re.compile(r"\b(?:i'm learning|i am learning|i'm studying|i study|i can|i know|i'm good at|i am good at|good at)\s+(.+)", re.I)


def _goal_key(value: str) -> str:
    v = value.lower()
    if any(w in v for w in ("learn", "study", "master")):
        return "learning"
    if any(w in v for w in ("weight", "run", "gym", "fit", "marathon")):
        return "fitness"
    if any(w in v for w in ("become", "job", "career", "promotion", "startup")):
        return "career"
    if any(w in v for w in ("save", "money", "invest", "financial")):
        return "financial"
    return "personal"


def mock_extract(user: str) -> list[dict[str, Any]]:
    """Deterministic operation-emitting extraction for the mock provider.

    Public (no underscore) so tests can reference the same rules.
    """
    if not user or not user.strip():
        return []
    out: list[dict[str, Any]] = []

    # 0. Elliptical answer to a pending-slot question (PART 4/6).
    # The extraction prompt wraps context + "TARGET message:" + a HINT. Resolve
    # a short duration answer onto the node named in the hint.
    tm = _TARGET_RE.search(user)
    target = tm.group(1).strip() if tm else user.strip()
    hint = _HINT_RE.search(user)
    if hint and len(target.split()) <= 6:  # short answer, not a full statement
        md = _DUR_RE.search(target)
        lm = _LABEL_RE.match(hint.group(2).strip())
        if md and lm:
            dim, key, val = lm.group(1), lm.group(2), lm.group(3)
            unit = md.group(2) if md.group(2).endswith("s") else md.group(2) + "s"
            a = _assertion(dim, key, val, operation="REFINE", quote=target)
            if a:
                a["target_node"] = {"dimension": dim, "key": key, "value": val}
                a["attributes"] = [{"name": hint.group(1), "value": md.group(1), "unit": unit}]
                return [a]

    text = target  # extract only from the target message

    # 0b. Relationship breakup END ("i broke up with hailey").
    m = _BREAKUP_RE.search(text)
    if m:
        name = m.group(1)
        a = _assertion("relationship", name, name, operation="END", quote=text, polarity="negate")
        if a:  # resolve_end matches by value within the relationship dimension
            out.append(a)
        return out

    # 0c. "my dog name is herald" -> pet node + name attribute (NOT identity).
    m = _MY_NOUN_NAME_RE.search(text)
    if m and m.group(1).lower() != "name":
        noun, pname = m.group(1).lower(), m.group(2)
        key, val = ("pet", noun) if noun in _PETS else (noun, noun)
        a = _assertion("relationship", key, val, operation="ADD", quote=text,
                       subject=f"relationship/{key}={val}",
                       attributes=[{"name": "name", "value": pname}])
        if a:
            out.append(a)
        return out

    # 0d. "my brother is a doctor" -> occupation attribute on the relationship.
    m = _MY_REL_OCC_RE.search(text)
    if m:
        role, occ = m.group(1), ontology.normalize_value(m.group(2))
        a = _assertion("relationship", role, role, operation="ADD", quote=text,
                       subject=f"relationship/{role}={role}",
                       attributes=[{"name": "occupation", "value": occ}])
        if a:
            out.append(a)
        return out

    # 1. Identity — name.
    m = _NAME_RE.search(text)
    if m:
        a = _assertion("identity", "name", m.group(1), operation="ADD", quote=text, conf=0.95)
        if a:
            out.append(a)
        return out

    # 1b. Relationship "my girlfriend is hailey" -> relationship/girlfriend = hailey.
    m = _MY_REL_RE.search(text)
    if m:
        role, name = m.group(1), m.group(2)
        a = _assertion("relationship", role, name, operation="ADD", quote=text)
        if a:
            out.append(a)
        return out

    # 2. Explicit replacement ("switched from X to Y").
    m = _SWAP_RE.search(text)
    if m:
        old, new = m.group(1), m.group(2)
        cls = _classify(new)
        if cls:
            dim, key, val = cls
            old_cls = _classify(old)
            replaces = old_cls[2] if old_cls else old
            a = _assertion(dim, key, val, operation="ADD", replaces=replaces, quote=text)
            if a:
                out.append(a)
        return out
    m = _SWAP_TO_RE.search(text)
    if m:
        cls = _classify(m.group(1))
        if cls:
            dim, key, val = cls
            a = _assertion(dim, key, val, operation="ADD", quote=text)
            if a:
                out.append(a)
        return out

    # 3. Diet (SINGLE) — check before generic patterns.
    lower = text.lower()
    diet = None
    if "vegetarian" in lower:
        diet = "vegetarian"
    elif "vegan" in lower:
        diet = "vegan"
    elif "pescatarian" in lower:
        diet = "pescatarian"
    elif re.search(r"\beats?\s+meat\b|\bnon-?veg\b|\bmeat now\b", lower):
        diet = "eats meat"
    if diet and not _END_RE.search(text):
        a = _assertion("preference", "diet", diet, operation="ADD", quote=text)
        if a:
            out.append(a)
        return out

    # 4. Cessation / negation -> END.
    if _END_RE.search(text):
        subject = _END_RE.sub("", text)
        subject = re.sub(r"\b(i|i'm|i am|doing|the|my|now|anymore)\b", " ", subject, flags=re.I)
        cls = _classify(subject)
        if cls:
            dim, key, val = cls
            a = _assertion(dim, key, val, operation="END", quote=text, polarity="negate")
            if a:
                out.append(a)
        return out

    # 4b. Identity slots (SINGLE): location, employer, age, occupation.
    for regex, key in (
        (r"\bi (?:live in|reside in|'m from|am from|'m based in|am based in)\s+(.+)", "location"),
        (r"\bi (?:work at|work for|'m at|am at)\s+(.+)", "employer"),
    ):
        m = re.search(regex, text, re.I)
        if m:
            a = _assertion("identity", key, m.group(1), operation="ADD", quote=text)
            if a:
                out.append(a)
            return out
    m = re.search(r"\bi(?:'m| am)\s+(\d{1,2})\b(?!\s*%)", text, re.I)
    if m:
        a = _assertion("identity", "age_range", m.group(1), operation="ADD", quote=text)
        if a:
            out.append(a)
        return out
    m = re.search(r"\bi (?:work as|am|'m)\s+an?\s+(.+)", text, re.I)
    if m:
        a = _assertion("identity", "occupation", m.group(1), operation="ADD", quote=text)
        if a:
            out.append(a)
        return out

    # 4c. Skill/experience with a duration -> value + years_experience attribute.
    m = re.search(r"\b(?:coding|programming|worked|working|experience)\s+(?:in|with)?\s*([a-z0-9+#]+).*?(\d+)\s*(year|years|month|months)", lower)
    if m:
        lang, n = m.group(1), m.group(2)
        unit = "years" if "year" in m.group(3) else "months"
        a = _assertion("skill", lang, lang, operation="ADD", quote=text)
        if a:
            a["attributes"] = [{"name": "years_experience", "value": n, "unit": unit}]
            out.append(a)
        return out

    # 4d. "my favourite <thing> is <value ...>" -> preference + decomposed value.
    m = re.search(r"\bmy (?:favou?rite|fav)\s+(\w+)\s+is\s+(.+)", text, re.I)
    if m:
        subj = ontology.normalize_key(m.group(1))
        a = _assertion("preference", f"favourite_{subj}", m.group(2), operation="ADD", quote=text)
        if a:
            fm = re.search(r"fermentation\s+(?:over|of)?\s*(\d+)\s*(year|years|month|months)", lower)
            if fm:
                a["attributes"] = [{"name": "fermentation_duration", "value": fm.group(1),
                                    "unit": "years" if "year" in fm.group(2) else "months"}]
            out.append(a)
        return out

    # 4e. "i started drinking wine" -> habit.
    m = _STARTED_RE.search(text)
    if m:
        val = ontology.normalize_value(m.group(1))
        if not ontology.is_valid_value(val):
            val = ontology.decompose_value(val) or val
        a = _assertion("habit", val, val, operation="ADD", quote=text)
        if a:
            out.append(a)
        return out

    # 4f. "X is part of Y" -> create both concept nodes (edge added via relations).
    m = re.search(r"(.+?)\s+is part of\s+(?:my\s+)?(.+)", text, re.I)
    if m:
        for phrase in (m.group(1), m.group(2)):
            cls = _classify(phrase)
            if cls:
                a = _assertion(cls[0], cls[1], cls[2], operation="ADD", quote=text)
                if a:
                    out.append(a)
        return out

    # 4g. Dislike ("i hate momos") -> preference/food_dislikes.
    m = _HATE_RE.search(text)
    if m:
        cls = _classify(m.group(1))
        if cls and cls[1] == "food_likes":
            a = _assertion("preference", "food_dislikes", cls[2], operation="ADD", quote=text)
            if a:
                out.append(a)
        return out

    # 5. Preference / interest ("i love X").
    m = _LOVE_RE.search(text)
    if m:
        cls = _classify(m.group(1))
        if cls:
            dim, key, val = cls
            a = _assertion(dim, key, val, operation="ADD", quote=text)
            if a:
                out.append(a)
        return out

    # 6. Skill.
    m = _SKILL_RE.search(text)
    if m:
        val = ontology.normalize_value(m.group(1))
        a = _assertion("skill", val, val, operation="ADD", quote=text)
        if a:
            out.append(a)
        return out

    # 7. Goal.
    m = _GOAL_RE.search(text)
    if m:
        val = ontology.normalize_value(m.group(1))
        a = _assertion("goal", _goal_key(val), val, operation="ADD", quote=text)
        if a:
            out.append(a)
        return out

    # 8. Relationship.
    m = re.search(r"\bmy (" + "|".join(_RELATION_ROLES) + r")\b", lower)
    if m:
        role = m.group(1)
        a = _assertion("relationship", role, role, operation="ADD", quote=text)
        if a:
            out.append(a)
        return out

    return out
