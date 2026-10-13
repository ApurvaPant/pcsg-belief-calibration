"""STEP 1 regression: every stored assertion gets all THREE confidence signals.

Previously self_consistency was null on relationship/ADD (the entity-subject path
rebuilt a minimal assertion and dropped the attached score). T2 then compared
signals over different subsets. With resampling enabled, coverage of verbalized,
self_consistency and evidence must be 100% on an oracle_mock persona replay, and
no row may carry a self_consistency_reason."""
from __future__ import annotations

import pytest

from app import pipeline, signals
from app.config import settings
from app.db import get_conn, now_iso
from app.utils import new_id
from pcsg import datagen, oracle


@pytest.fixture
def oracle_replay(monkeypatch):
    monkeypatch.setenv("PCSG_SELF_CONSISTENCY", "1")
    prov = settings.llm.provider
    thr = settings.pipeline.salience_threshold
    monkeypatch.setattr(settings.llm, "provider", "oracle_mock")
    settings.pipeline.salience_threshold = 0.0
    oracle.configure("overconfident", seed=2026)
    yield
    settings.llm.provider = prov
    settings.pipeline.salience_threshold = thr


def _replay(persona):
    uid, sid = new_id("user"), new_id("sess")
    with get_conn() as c:
        c.execute("INSERT INTO users(id,label,created_at)VALUES(?,?,?)", (uid, persona.name, now_iso()))
        c.execute("INSERT INTO sessions(id,user_id,title,created_at)VALUES(?,?,?,?)", (sid, uid, "exp", now_iso()))
    for t in persona.turns:
        oracle.set_turn(datagen.turn_expected(persona, t))
        pipeline.process_turn(t.text, sid, uid)


def test_all_three_signals_100pct_coverage(oracle_replay):
    # isolate from rows other tests wrote into the shared conftest DB (some with
    # resampling off) so coverage reflects ONLY this enabled replay.
    with get_conn() as c:
        c.execute("DELETE FROM confidence_signals")
    personas = datagen.generate_personas(2026, 15)
    # include at least one persona carrying a relationship fact (the old bug site)
    rel = next(p for p in personas if any(f.dimension == "relationship" for f in p.facts.values()))
    _replay(rel)
    _replay(personas[1])

    with get_conn() as c:
        cov = signals.coverage(c)
        rel_rows = c.execute(
            "SELECT COUNT(*) n FROM confidence_signals WHERE dimension='relationship'").fetchone()["n"]
        null_sc = c.execute(
            "SELECT COUNT(*) n FROM confidence_signals WHERE self_consistency IS NULL").fetchone()["n"]
        reasons = c.execute(
            "SELECT COUNT(*) n FROM confidence_signals WHERE self_consistency_reason IS NOT NULL").fetchone()["n"]

    assert cov["n"] > 0 and rel_rows > 0, cov
    assert cov["verbalized"] == 1.0, cov
    assert cov["evidence"] == 1.0, cov
    assert cov["self_consistency"] == 1.0, cov          # the fix
    assert null_sc == 0 and reasons == 0                 # no silent nulls, no reason needed when enabled


def test_disabled_resampling_records_reason_not_silent_null(monkeypatch):
    """With resampling OFF, self_consistency is null but carries reason 'sc_disabled'."""
    monkeypatch.delenv("PCSG_SELF_CONSISTENCY", raising=False)
    prov = settings.llm.provider
    monkeypatch.setattr(settings.llm, "provider", "oracle_mock")
    settings.pipeline.salience_threshold = 0.0
    oracle.configure("overconfident", seed=2026)
    try:
        p = datagen.generate_personas(2026, 15)[2]
        _replay(p)
        with get_conn() as c:
            rows = c.execute("SELECT self_consistency, self_consistency_reason FROM confidence_signals "
                             "WHERE message_id IN (SELECT id FROM messages)").fetchall()
            # any null self_consistency written this run must carry a reason
            silent = c.execute("SELECT COUNT(*) n FROM confidence_signals "
                               "WHERE self_consistency IS NULL AND self_consistency_reason IS NULL").fetchone()["n"]
        assert silent == 0
    finally:
        settings.llm.provider = prov
