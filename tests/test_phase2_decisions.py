"""Phase 2 - decision record: the lifecycle rules hold under attempts to break them."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from financial_brain.config import Config
from financial_brain.decisions import record as dr
from financial_brain.evidence import ledger
from financial_brain.storage.db import Database

AS_OF = datetime(2026, 9, 21, 9, 0)


@pytest.fixture
def db(tmp_path) -> Database:
    d = Database(Config(data_root=tmp_path).ensure())
    d.migrate()
    return d


def _ev(con, text, published):
    return ledger.mint(con, kind="announcement", subject="INE092A01019", as_of=published,
                       published_at=published, claim=text, value={"t": text}, source="BSE",
                       source_tier=1, derivation="test")


def _setup(con, *, late=False):
    con.execute("""INSERT INTO world_states VALUES ('ws_test', DATE '2026-09-18', NOW(), ?, 0,
                   'test')""", [json.dumps({"as_of": AS_OF.isoformat()})])
    pro = _ev(con, "Q1 volume guidance raised", datetime(2026, 9, 18, 16, 0))
    con_ = _ev(con, "Soda-ash prices at a 3-year low",
               datetime(2026, 9, 22, 10, 0) if late else datetime(2026, 9, 18, 17, 0))
    return dr.Decision(isin="INE092A01019", action="WATCH", horizon_days=90,
                       thesis="Margins trough in H2 as soda-ash capacity closes",
                       world_state_version="ws_test", supporting_evidence=[pro],
                       contrary_evidence=[con_],
                       primary_uncertainty="timing of Chinese capacity exits",
                       invalidation_conditions=["soda-ash below $250/t for 2 quarters"],
                       sizing={"weight": 0.02}, author="agent:committee")


def test_the_happy_path_to_paper(db):
    with db.connect() as con:
        did = dr.draft(con, _setup(con))
        for _ in range(3):
            dr.advance(con, did, actor="agent:committee")
        assert dr._state(con, did) == "PAPER_CANDIDATE"
        used = con.execute("SELECT COUNT(*) FROM evidence_use WHERE used_by_kind='decision'"
                           ).fetchone()[0]
    assert used == 2, "every cited claim records that a decision relied on it"


def test_hindsight_evidence_is_refused(db):
    with db.connect() as con:
        did = dr.draft(con, _setup(con, late=True))
        with pytest.raises(dr.DecisionError, match="hindsight"):
            dr.advance(con, did, actor="agent:committee")


def test_superseded_evidence_is_refused(db):
    with db.connect() as con:
        d = _setup(con)
        ledger.correct(con, d.contrary_evidence[0], kind="announcement", subject="x",
                       as_of=datetime(2026, 9, 18, 18, 0), claim="corrected",
                       value={"t": "corrected"}, source="BSE", source_tier=1,
                       derivation="test")
        did = dr.draft(con, d)
        with pytest.raises(dr.DecisionError, match="superseded"):
            dr.advance(con, did, actor="agent:committee")


@pytest.mark.parametrize("field, value, message", [
    ("contrary_evidence", [], "both supporting and contrary"),
    ("primary_uncertainty", "  ", "primary uncertainty"),
    ("invalidation_conditions", [], "invalidation"),
])
def test_a_one_sided_decision_cannot_be_verified(db, field, value, message):
    with db.connect() as con:
        d = _setup(con)
        setattr(d, field, value)
        did = dr.draft(con, d)
        with pytest.raises(dr.DecisionError, match=message):
            dr.advance(con, did, actor="agent:committee")


def test_an_agent_can_never_approve(db):
    with db.connect() as con:
        did = dr.draft(con, _setup(con))
        for _ in range(3):
            dr.advance(con, did, actor="agent:committee")
        with pytest.raises(dr.DecisionError, match="only a human"):
            dr.advance(con, did, actor="agent:portfolio_manager")
        assert dr.advance(con, did, actor="owner") == "HUMAN_APPROVED"


def test_no_broker_path_while_execution_is_disabled(db):
    with db.connect() as con:
        did = dr.draft(con, _setup(con))
        for _ in range(3):
            dr.advance(con, did, actor="agent:committee")
        dr.advance(con, did, actor="owner")
        with pytest.raises(dr.DecisionError, match="execution is disabled"):
            dr.advance(con, did, actor="owner")


def test_closing_needs_a_reason_and_history_is_kept(db):
    with db.connect() as con:
        did = dr.draft(con, _setup(con))
        with pytest.raises(dr.DecisionError, match="reason"):
            dr.close(con, did, to="REJECTED", actor="owner", note="")
        dr.close(con, did, to="REJECTED", actor="owner", note="thesis already priced in")
        with pytest.raises(dr.DecisionError, match="nothing follows"):
            dr.advance(con, did, actor="owner")
        h = dr.history(con, did)
    assert [e["to_state"] for e in h] == ["DRAFT", "REJECTED"]
    assert h[-1]["note"] == "thesis already priced in"


def test_the_outcome_window_must_elapse(db):
    with db.connect() as con:
        did = dr.draft(con, _setup(con))
        for actor in ["agent:c"] * 3 + ["owner"]:
            dr.advance(con, did, actor=actor)
        dr.advance(con, did, actor="owner", execution_enabled=True)
        dr.advance(con, did, actor="owner", execution_enabled=True)       # EXECUTED
        with pytest.raises(dr.DecisionError, match="outcome window"):
            dr.advance(con, did, actor="owner")
