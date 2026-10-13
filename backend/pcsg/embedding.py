"""Local, offline sentence embeddings for the vector_rag baseline (STEP 2).

Preferred: `sentence-transformers/all-MiniLM-L6-v2` (downloaded once from Hugging
Face, then fully offline, no API). If that model/import is unavailable, falls back
to a deterministic hashed bag-of-tokens vector so the retrieval plumbing still
runs offline (the dry run is synthetic anyway). `backend()` reports which is in use.

No LLM-provider network call is ever made here; this is embeddings only and does
not go through app/llm.py (that module is for completion/embedding *provider*
calls — the RAG index is local).
"""
from __future__ import annotations

import hashlib
import math
import os

import numpy as np

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
_MODEL = None
_BACKEND = None
_FALLBACK_DIM = 384  # match MiniLM's dim so the index shape is identical


def _load():
    global _MODEL, _BACKEND
    if _BACKEND is not None:
        return
    if os.getenv("PCSG_FORCE_FALLBACK_EMBED") == "1":
        _MODEL, _BACKEND = None, "fallback-hash"
        return
    try:
        from sentence_transformers import SentenceTransformer
        _MODEL = SentenceTransformer(MODEL_NAME)
        _BACKEND = MODEL_NAME
    except Exception:  # noqa: BLE001 — model not installed/available: deterministic fallback
        _MODEL, _BACKEND = None, "fallback-hash"


def backend() -> str:
    _load()
    return _BACKEND or "fallback-hash"


def _hash_embed(text: str, dim: int = _FALLBACK_DIM) -> np.ndarray:
    vec = np.zeros(dim, dtype=float)
    for tok in (text or "").lower().split():
        h = int(hashlib.sha1(tok.encode()).hexdigest(), 16)
        vec[h % dim] += 1.0
    n = math.sqrt(float(vec @ vec)) or 1.0
    return vec / n


def embed(texts: list[str]) -> np.ndarray:
    """Return an (n, d) L2-normalised matrix."""
    _load()
    if _MODEL is not None:
        return np.asarray(_MODEL.encode(list(texts), normalize_embeddings=True), dtype=float)
    return np.vstack([_hash_embed(t) for t in texts]) if texts else np.zeros((0, _FALLBACK_DIM))


def top_k(query: str, docs: list[str], k: int = 5) -> list[tuple[int, float]]:
    """Return [(doc_index, cosine_similarity)] for the top-k docs by cosine.
    Vectors are L2-normalised so cosine == dot product."""
    if not docs:
        return []
    q = embed([query])[0]
    D = embed(docs)
    sims = D @ q
    order = np.argsort(-sims)[: max(1, k)]
    return [(int(i), float(sims[i])) for i in order]
