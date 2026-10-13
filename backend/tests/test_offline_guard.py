"""Offline guard + served-model surface + rate-based fault injection.

PCSG_OFFLINE=1 must make llm.py refuse ANY non-mock provider call immediately
(before a network client is constructed), so an offline session can never spend.
The served-model field and the rate fault injector support the runner's cost /
retry exercises with no network and no spend.
"""
from __future__ import annotations

import httpx
import pytest

from app import llm
from app.config import settings


@pytest.fixture
def restore_llm(monkeypatch):
    """Snapshot provider + fault state; restore after the test."""
    prov = settings.llm.provider
    yield monkeypatch
    settings.llm.provider = prov
    llm.set_fault(None)
    llm.set_fault_rates(None)
    llm.RETRY_LOG.clear()


def test_offline_guard_blocks_non_mock(restore_llm):
    restore_llm.setenv("PCSG_OFFLINE", "1")
    settings.llm.provider = "openrouter"
    with pytest.raises(llm.OfflineGuardError):
        llm.complete([{"role": "user", "content": "hi"}], model="openai/gpt-4o-mini")


def test_offline_guard_blocks_embed_and_startup(restore_llm):
    restore_llm.setenv("PCSG_OFFLINE", "1")
    settings.llm.provider = "gemini"
    with pytest.raises(llm.OfflineGuardError):
        llm.embed(["x"])
    with pytest.raises(llm.OfflineGuardError):
        llm.startup_check()


def test_offline_guard_allows_mock(restore_llm):
    restore_llm.setenv("PCSG_OFFLINE", "1")
    settings.llm.provider = "mock"
    r = llm.complete([{"role": "user", "content": "i love pizza"}], model="m")
    assert r["text"]  # mock served it, no network, no raise


def test_served_model_echoed_by_mock(restore_llm):
    settings.llm.provider = "mock"
    r = llm.complete([{"role": "user", "content": "hi"}], model="requested-model-x")
    assert r["model"] == "requested-model-x"


def test_rate_fault_exhausts_retries_then_raises(restore_llm):
    settings.llm.provider = "mock"
    llm.RETRY_LOG.clear()
    llm.set_fault_rates({"http_500": 1.0}, seed=1)   # every call 500s
    with pytest.raises(httpx.HTTPStatusError):
        llm.complete([{"role": "user", "content": "hi"}], model="m")
    assert len(llm.RETRY_LOG) == llm._MAX_RETRIES    # at most 5 retries, all logged


def test_rate_fault_malformed_json_returned(restore_llm):
    settings.llm.provider = "mock"
    llm.set_fault_rates({"malformed_json": 1.0}, seed=2)
    r = llm.complete([{"role": "user", "content": "hi"}], model="m")
    assert r["text"].startswith("{not valid")       # soft fault surfaced, no crash


def test_rate_fault_recovers_at_low_rate(restore_llm):
    settings.llm.provider = "mock"
    llm.RETRY_LOG.clear()
    llm.set_fault_rates({"http_429": 0.3}, seed=7)   # some faults, recovers within budget
    r = llm.complete([{"role": "user", "content": "i love pizza"}], model="m")
    assert r["text"]
    assert len(llm.RETRY_LOG) <= llm._MAX_RETRIES


def test_cost_meter_accumulates(restore_llm):
    settings.llm.provider = "mock"
    llm.reset_cost()
    assert llm.spent_usd() == 0.0
    llm.complete([{"role": "user", "content": "i love pizza and momos"}], model="m")
    s = llm.cost_summary()
    assert s["calls"] == 1 and s["usd"] > 0 and s["prompt_tokens"] > 0
    llm.complete([{"role": "user", "content": "hi"}], model="m")
    assert llm.cost_summary()["calls"] == 2
    assert llm.spent_usd() > s["usd"]       # strictly increases
    llm.reset_cost()
    assert llm.spent_usd() == 0.0
