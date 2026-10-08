"""Domain constants shared across the pipeline and the API.

The slot ontology (dimensions + cardinality) now lives in `ontology.py`; this
module re-exports the pieces callers historically imported from here.
"""
from __future__ import annotations

from .ontology import (  # noqa: F401  (re-export)
    DIMENSIONS,
    STATUS_ACTIVE,
    STATUS_SUPERSEDED,
    STATUS_DELETED,
)
