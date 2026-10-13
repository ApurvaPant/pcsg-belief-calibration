"""STEP 3 — LongMemEval runner (T5) end-to-end on 3 mock questions (offline).

Uses oracle_mock + the deterministic fallback embedder; a tiny fixture stands in
for the dataset so no download is needed. Exercises: chronological haystack
replay, all four systems, abstention handling, per-type metrics, and resume."""
from __future__ import annotations

import json
import pathlib

import pytest

from app.config import settings
from pcsg import embedding, longmemeval_run

FIX = pathlib.Path(__file__).resolve().parent / "fixtures" / "longmemeval_mini.json"


@pytest.fixture(autouse=True)
def oracle_env(monkeypatch):
    monkeypatch.setenv("PCSG_FORCE_FALLBACK_EMBED", "1")
    embedding._BACKEND = None
    prov = settings.llm.provider
    thr = settings.pipeline.salience_threshold
    monkeypatch.setattr(settings.llm, "provider", "oracle_mock")
    from pcsg import oracle
    oracle.configure("perfect", seed=2026)   # perfect: full_context/pcsg should answer when gold present
    yield
    settings.llm.provider = prov
    settings.pipeline.salience_threshold = thr
    embedding._BACKEND = None


def test_longmemeval_three_questions_end_to_end(tmp_path):
    questions = json.loads(FIX.read_text(encoding="utf-8"))
    res = longmemeval_run.run(questions, tmp_path, dry_run=True, workers=1,
                              watermark="DRY RUN — SYNTHETIC")
    assert res["n_questions"] == 3
    assert res["count_by_type"] == {"knowledge-update": 2, "single-session-preference": 1}
    assert res["n_abstention"] == 1
    m = res["metrics"]
    for sysname in ("no_memory", "full_context", "vector_rag", "pcsg"):
        assert sysname in m and m[sysname]["n"] == 3
        assert 0.0 <= m[sysname]["accuracy_overall"] <= 1.0
        assert "accuracy_by_type" in m[sysname]
    # full_context sees the whole history, so it should get the answerable questions;
    # no_memory has nothing, so it should NOT beat full_context.
    assert m["full_context"]["accuracy_overall"] >= m["no_memory"]["accuracy_overall"]
    # abstention question: every system can correctly abstain -> abstention_accuracy defined
    assert m["no_memory"]["abstention_accuracy"] == 1.0   # no context -> always abstains correctly
    # output file written + schema-ish
    out = json.loads((tmp_path / "longmemeval.json").read_text())
    assert out["judge_source"] and out["metrics"]


def test_longmemeval_resume(tmp_path):
    questions = json.loads(FIX.read_text(encoding="utf-8"))
    longmemeval_run.run(questions[:1], tmp_path, dry_run=True)        # do 1
    assert json.loads((tmp_path / "lme_checkpoint.json").read_text())["done"] == ["ku_demo_1"]
    res = longmemeval_run.run(questions, tmp_path, dry_run=True)      # resume rest
    assert res["n_questions"] == 3                                     # 1 skipped + 2 new
    ids = [json.loads(l)["question_id"] for l in (tmp_path / "lme_records.jsonl").read_text().splitlines()]
    assert sorted(ids) == ["ku_demo_1", "ku_demo_abs", "ssp_demo_1"] and len(ids) == 3
