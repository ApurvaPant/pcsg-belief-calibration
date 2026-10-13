"""PHASE 1.4/1.5 — fill_prices.py parsing (offline, fixture-driven) and the
runner's live price-guard. No network, no model calls."""
from __future__ import annotations

import importlib.util
import json
import pathlib

import pytest

from pcsg.run import PriceConfigError, check_prices, model_mismatches

REPO = pathlib.Path(__file__).resolve().parents[2]
FIXTURE = pathlib.Path(__file__).resolve().parent / "fixtures" / "openrouter_models.json"


def _load_fill_prices():
    spec = importlib.util.spec_from_file_location("fill_prices", REPO / "scripts" / "fill_prices.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# ── fill_prices.parse_prices (pure parsing of the OpenRouter /models payload) ─ #
def test_parse_prices_converts_per_token_to_per_mtok():
    fp = _load_fill_prices()
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    prices, missing = fp.parse_prices(data, ["openai/gpt-4o", "openai/gpt-4o-mini", "google/gemini-2.5-flash"])
    assert prices["openai/gpt-4o"] == {"input": 2.5, "output": 10.0}
    assert prices["openai/gpt-4o-mini"] == {"input": 0.15, "output": 0.6}
    assert prices["google/gemini-2.5-flash"] == {"input": 0.3, "output": 2.5}
    assert missing == []


def test_parse_prices_flags_zero_absent_and_malformed():
    fp = _load_fill_prices()
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    prices, missing = fp.parse_prices(data, ["free/model", "weird/model", "nopricing/model", "not/listed"])
    assert prices == {}  # none of these yields a valid positive price
    assert set(missing) == {"free/model", "weird/model", "nopricing/model", "not/listed"}


# ── runner price-guard ────────────────────────────────────────────────────── #
def test_price_guard_allows_mock_regardless_of_prices():
    # mock / oracle_mock are free: no price needed, never raises.
    check_prices("mock", {"chat": "mock", "extractor": "mock"}, {})
    check_prices("oracle_mock", {"chat": "gemini-2.5-flash"}, {})


def test_price_guard_blocks_live_when_price_missing_or_zero():
    models = {"chat": "openai/gpt-4o", "extractor": "openai/gpt-4o-mini"}
    prices = {"openai/gpt-4o": {"input": 2.5, "output": 10.0}}  # gpt-4o-mini missing
    with pytest.raises(PriceConfigError) as e:
        check_prices("openrouter", models, prices)
    assert "openai/gpt-4o-mini" in str(e.value)

    prices_zero = {"openai/gpt-4o": {"input": 0, "output": 10.0},
                   "openai/gpt-4o-mini": {"input": 0.15, "output": 0.6}}
    with pytest.raises(PriceConfigError) as e2:
        check_prices("openrouter", models, prices_zero)
    assert "openai/gpt-4o" in str(e2.value)


def test_price_guard_passes_live_when_all_priced():
    models = {"chat": "openai/gpt-4o", "extractor": "openai/gpt-4o-mini", "x": "mock"}
    prices = {"openai/gpt-4o": {"input": 2.5, "output": 10.0},
              "openai/gpt-4o-mini": {"input": 0.15, "output": 0.6}}
    check_prices("openrouter", models, prices)  # no raise


# ── served≠requested model check ─────────────────────────────────────────── #
def test_model_mismatch_flagged():
    log = [
        {"role": "chat", "model_requested": "openai/gpt-4o", "model_served": "openai/gpt-4o"},
        {"role": "extractor", "model_requested": "openai/gpt-4o-mini", "model_served": "openai/gpt-3.5-turbo"},
        {"role": "judge", "model_requested": "openai/gpt-4o", "model_served": "openai/gpt-4o"},
    ]
    flagged = model_mismatches(log)
    assert len(flagged) == 1 and flagged[0]["role"] == "extractor"


def test_llm_surfaces_served_model_from_mock(monkeypatch):
    """A mock that returns a different served model is observable at complete()."""
    from app import llm
    from app.config import settings
    monkeypatch.setattr(settings.llm, "provider", "mock")
    monkeypatch.setattr(llm, "_mock_complete",
                        lambda messages: {"text": "x", "prompt_tokens": 1, "completion_tokens": 1,
                                          "latency_ms": 1, "model": "served-different"})
    r = llm.complete([{"role": "user", "content": "hi"}], model="requested-X")
    assert r["model"] == "served-different"
    assert model_mismatches([{"model_requested": "requested-X", "model_served": r["model"]}])
