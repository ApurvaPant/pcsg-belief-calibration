"""Regression: cost.json must not be zeroed by a resume/offline invocation that makes
no model calls. `_replay` resets the cost meter, so writing the raw summary would clobber
the original run's real cumulative cost. write_cost_json() preserves a prior calls>0 file
when the current summary has calls==0."""
from __future__ import annotations

import json

from pcsg.run import write_cost_json


def test_zero_call_invocation_preserves_prior(tmp_path):
    p = tmp_path / "cost.json"
    real = {"usd": 0.39, "calls": 1920, "prompt_tokens": 507797, "completion_tokens": 92343}
    p.write_text(json.dumps(real), encoding="utf-8")
    # a resume/offline stage made zero calls -> must NOT clobber
    write_cost_json(p, {"usd": 0.0, "calls": 0, "prompt_tokens": 0, "completion_tokens": 0})
    kept = json.loads(p.read_text())
    assert kept["calls"] == 1920 and kept["prompt_tokens"] == 507797


def test_real_calls_overwrite(tmp_path):
    p = tmp_path / "cost.json"
    p.write_text(json.dumps({"calls": 10, "prompt_tokens": 100}), encoding="utf-8")
    write_cost_json(p, {"usd": 1.0, "calls": 50, "prompt_tokens": 999})
    out = json.loads(p.read_text())
    assert out["calls"] == 50 and out["prompt_tokens"] == 999


def test_first_write_when_absent(tmp_path):
    p = tmp_path / "cost.json"
    write_cost_json(p, {"calls": 0, "prompt_tokens": 0})
    assert p.exists() and json.loads(p.read_text())["calls"] == 0
