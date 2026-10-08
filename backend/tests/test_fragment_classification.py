"""Regression: a new fact stated right after the assistant asked a question must
not be misclassified as a fragment answer and swallowed by the slot/refine path.

Found in multi-turn testing: "actually, call me johnny now" left the name
unchanged because it lacked a recognised first-person pronoun, so it was treated
as a fragment and force-attached as an attribute instead of superseding."""
from __future__ import annotations

from app.pipeline import _is_fragment


def test_complete_clauses_are_not_fragments():
    for s in [
        "call me johnny",
        "actually, call me johnny now",
        "his name is rocky",
        "her name is hailey",
        "my dog's name is max",
        "i'm johnny",
        "i switched to rust",   # first-person subject
    ]:
        assert _is_fragment(s) is False, s


def test_true_fragments_still_detected():
    for s in ["8 months", "about 6 months", "mostly sci-fi", "every weekend", "technical"]:
        assert _is_fragment(s) is True, s
