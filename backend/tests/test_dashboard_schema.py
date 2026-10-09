"""dashboard.json data contract (viz STEP 1): the producer output validates, and
the validator rejects malformed input."""
from __future__ import annotations

import pathlib

from pcsg.viz import schema
from pcsg.viz.build import build_dashboard

RES = pathlib.Path(__file__).resolve().parent.parent.parent / "results"


def _valid():
    return {
        "meta": {"run_id": "r", "git_sha": "abc", "mode": "dry-run", "date": "r",
                 "models": {}, "sample_sizes": {}},
        "watermark": "DRY RUN — SYNTHETIC DATA, NOT RESULTS",
        "kpi": {}, "baselines": [], "token_growth": [], "gate_funnel": {},
        "cost_curve": {}, "calibration": {}, "abstention": {}, "persona_replay": [],
    }


def test_valid_dashboard_passes():
    assert schema.validate(_valid()) == []


def test_missing_section_fails():
    d = _valid(); del d["calibration"]
    assert any("calibration" in e for e in schema.validate(d))
    d2 = _valid(); d2["meta"] = {"run_id": "r"}
    assert schema.validate(d2)  # missing meta fields


def test_built_dashboard_validates_if_run_exists():
    runs = sorted((RES / "runs").glob("*/config.json")) if (RES / "runs").exists() else []
    if not runs:
        import pytest
        pytest.skip("no run under results/runs/ (run pcsg.run --dry-run first)")
    dash = build_dashboard(runs[-1].parent)
    assert schema.validate(dash) == []
    # the new offline series are present and non-trivial
    assert len(dash["token_growth"]) == len(schema.TOKEN_GROWTH_TURNS)
    assert dash["gate_funnel"]["stages"]
