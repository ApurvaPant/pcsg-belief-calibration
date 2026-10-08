"""Tier-0 gate: the required categories must pass; rejects are narrow."""
from __future__ import annotations

import pytest

from app.salience import is_salient, score_salience

MUST_PASS = [
    "my name is daksh",
    "call me dan",
    "i am a pilot",
    "i stopped doing jogging",
    "i no longer eat meat",
    "i used to smoke",
    "I switched from Python to Rust",
    "i moved from london to berlin",
    "i love machine learning",
    "i usually wake up at 6",
    "i want to become a pilot",
    "i'm vegetarian",
    "my favorite food is momos",
]

MUST_REJECT = [
    "thanks!",
    "ok cool",
    "haha nice",
    "good morning",
    "what can you do?",
    "how are you?",
    "can you help me?",
    "yes",
    "lol",
]


@pytest.mark.parametrize("msg", MUST_PASS)
def test_required_signals_pass(msg):
    score, *_ = score_salience(msg)
    assert is_salient(score), f"false negative: {msg!r}"


@pytest.mark.parametrize("msg", MUST_REJECT)
def test_pleasantries_and_questions_reject(msg):
    score, *_ = score_salience(msg)
    assert not is_salient(score), f"leaked: {msg!r}"


def test_reject_rate_is_meaningful():
    corpus = MUST_PASS + MUST_REJECT
    rejected = sum(1 for m in corpus if not is_salient(score_salience(m)[0]))
    # Narrow gate: rejects the filler, keeps all real state.
    assert rejected == len(MUST_REJECT)
