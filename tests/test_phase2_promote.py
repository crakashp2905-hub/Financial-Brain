"""Walking drafts toward paper (C17 -> C23).

The scorecard can only judge what reached paper. This moves drafts there and no further:
paper is not money, so an agent may promote to it, but HUMAN_APPROVED stays the owner's.
The refusals matter as much as the promotions, so they are reported with their reason.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from financial_brain.config import Config
from financial_brain.decisions import promote
from financial_brain.decisions import record as dr
from financial_brain.storage.db import Database


@pytest.fixture
def con(tmp_path):
    db = Database(Config(data_root=tmp_path).ensure())
    db.migrate()
    with db.connect() as c:
        # A paper trade enters at the *adjusted* close, through the lineage the security
        # master keeps, so those are the objects to stub - not eod_prices.
        c.execute("""CREATE OR REPLACE VIEW adjusted_prices AS SELECT * FROM (VALUES
            ('L1', DATE '2026-09-18', CAST(100.0 AS DOUBLE)))
            t(lineage, business_date, close_adj)""")
        c.execute("""CREATE OR REPLACE VIEW security_lineage AS
                     SELECT 'INE000A01001' AS isin, 'L1' AS lineage""")
        yield c


def _world_state(con, as_of="2026-09-18 15:30:00"):
    import json
    con.execute("""INSERT INTO world_states (version_id, business_date, built_at, content,
                   evidence_count, builder) VALUES ('ws_1', DATE '2026-09-18', NOW(), ?, 2,
                   'test')""", [json.dumps({"as_of": as_of})])


def _evidence(con, eid, published="2026-09-18 10:00:00"):
    con.execute("""INSERT INTO evidence (evidence_id, kind, subject, as_of, published_at,
                   claim, value, source, source_tier, derivation, confidence, quality,
                   observed_at) VALUES (?, 'x', 'INE000A01001', ?, ?, 'c', '{}', 'BSE', 1,
                   'd', 'high', 'ok', NOW())""", [eid, published, published])


def _draft(con, **over):
    kwargs = dict(isin="INE000A01001", action="BUY", horizon_days=90, thesis="t",
                  world_state_version="ws_1", supporting_evidence=["ev_a"],
                  contrary_evidence=["ev_b"], primary_uncertainty="margins",
                  invalidation_conditions=["falls 15%"], sizing={"weight": 0.03},
                  author="agent:committee")
    kwargs.update(over)
    return dr.draft(con, dr.Decision(**kwargs))


def test_a_complete_draft_walks_to_paper_and_opens_a_trade(con, monkeypatch):
    _world_state(con)
    _evidence(con, "ev_a")
    _evidence(con, "ev_b")
    did = _draft(con)
    monkeypatch.setattr("financial_brain.constitution.rules.check", lambda *a, **k: [])
    out = promote.run(con)
    assert out["reached_paper"] == 1
    assert dr._state(con, did) == "PAPER_CANDIDATE"


def test_a_draft_missing_contrary_evidence_stops_with_the_reason(con, monkeypatch):
    _world_state(con)
    _evidence(con, "ev_a")
    did = _draft(con, contrary_evidence=[], supporting_evidence=["ev_a"])
    monkeypatch.setattr("financial_brain.constitution.rules.check", lambda *a, **k: [])
    out = promote.run(con)
    assert out["reached_paper"] == 0
    assert "supporting and contrary evidence" in out["blocked"][0]["why"]
    assert dr._state(con, did) == "DRAFT"


def test_the_constitution_blocking_a_buy_is_reported_not_swallowed(con, monkeypatch):
    """'3 of 5 drafts were blocked by the concentration rule' is a finding about the
    committee, not an error to hide."""
    _world_state(con)
    _evidence(con, "ev_a")
    _evidence(con, "ev_b")
    _draft(con)
    monkeypatch.setattr("financial_brain.constitution.rules.check",
                        lambda *a, **k: [{"rule": "max position 2%", "fact": "weight 3%"}])
    out = promote.run(con)
    assert out["reached_paper"] == 0
    assert "constitution: max position 2%" in out["blocked"][0]["why"]


def test_promotion_never_reaches_human_approval(con, monkeypatch):
    _world_state(con)
    _evidence(con, "ev_a")
    _evidence(con, "ev_b")
    did = _draft(con)
    monkeypatch.setattr("financial_brain.constitution.rules.check", lambda *a, **k: [])
    promote.run(con)
    promote.run(con)                       # again, in case it would keep walking
    assert dr._state(con, did) == "PAPER_CANDIDATE", "only a human approves"


def test_a_watch_call_is_not_paper_traded(con, monkeypatch):
    _world_state(con)
    _evidence(con, "ev_a")
    _evidence(con, "ev_b")
    _draft(con, action="WATCH", sizing={})
    monkeypatch.setattr("financial_brain.constitution.rules.check", lambda *a, **k: [])
    out = promote.run(con)
    assert out["not_tradeable"] == 1 and out["reached_paper"] == 0


def test_a_decision_waiting_for_tomorrows_price_is_retried_not_abandoned(con, monkeypatch):
    """A trade must enter at a price that existed after the decision. Until the next
    session is ingested there is none - and the decision must not be stranded at paper."""
    _world_state(con, as_of="2026-09-30 15:30:00")
    _evidence(con, "ev_a", published="2026-09-30 10:00:00")
    _evidence(con, "ev_b", published="2026-09-30 10:00:00")
    did = _draft(con)
    con.execute("UPDATE decisions SET created_at = ? WHERE decision_id = ?",
                [datetime(2026, 9, 30, tzinfo=timezone.utc), did])
    monkeypatch.setattr("financial_brain.constitution.rules.check", lambda *a, **k: [])
    first = promote.run(con)
    assert first["awaiting_price"] == 1 and first["blocked"] == []
    again = promote.run(con)
    assert again["considered"] == 1, "still picked up, because it has no trade yet"
    con.execute("""CREATE OR REPLACE VIEW adjusted_prices AS SELECT * FROM (VALUES
        ('L1', DATE '2026-10-01', CAST(101.0 AS DOUBLE)))
        t(lineage, business_date, close_adj)""")
    assert promote.run(con)["traded"] == 1, "the trade opens by itself once a price exists"


def test_a_traded_decision_is_not_considered_again(con, monkeypatch):
    _world_state(con)
    _evidence(con, "ev_a")
    _evidence(con, "ev_b")
    _draft(con)
    monkeypatch.setattr("financial_brain.constitution.rules.check", lambda *a, **k: [])
    first = promote.run(con)
    assert first["traded"] == 1
    assert promote.run(con)["considered"] == 0, "a decision with a trade is done here"
    assert con.execute("SELECT COUNT(*) FROM paper_trades").fetchone()[0] == 1
