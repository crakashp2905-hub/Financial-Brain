"""Invalidation monitoring (C17): a condition nobody re-checks is decoration.

Phase 2's exit test asks for theses "whose invalidation conditions are actively
monitored". These tests pin what that means here: typed conditions are re-evaluated
against Tier-1 data, a trigger becomes dated evidence, and prose with no typed twin is
reported as unmonitored rather than assumed satisfied.
"""
from __future__ import annotations

from datetime import date

import pytest

from financial_brain.config import Config
from financial_brain.decisions import monitor
from financial_brain.decisions import record as dr
from financial_brain.storage.db import Database


@pytest.fixture
def con(tmp_path):
    db = Database(Config(data_root=tmp_path).ensure())
    db.migrate()
    with db.connect() as c:
        c.execute("""CREATE OR REPLACE VIEW eod_prices AS SELECT * FROM (VALUES
            ('INE000A01001', DATE '2026-09-18', CAST(850.0 AS DOUBLE)),
            ('INE000A01001', DATE '2026-09-10', CAST(1000.0 AS DOUBLE)))
            t(isin, business_date, close_price)""")
        yield c


def _draft(con, **over):
    kwargs = dict(isin="INE000A01001", action="BUY", horizon_days=90, thesis="cheap",
                  world_state_version="ws_1", supporting_evidence=["ev_a"],
                  contrary_evidence=["ev_b"], primary_uncertainty="margins",
                  invalidation_conditions=["falls 15% from entry"],
                  invalidation_checks=[{"check": "drawdown_from", "reference": 1000.0,
                                        "pct": 0.15}])
    kwargs.update(over)
    return dr.draft(con, dr.Decision(**kwargs))


def test_a_typed_condition_is_evaluated_against_prices(con):
    did = _draft(con)
    ev = monitor.evaluate(con, did, date(2026, 9, 18))
    assert [r["state"] for r in ev["results"]] == ["triggered"]
    assert "850.00" in ev["results"][0]["detail"] and "1000.00" in ev["results"][0]["detail"]


def test_a_thesis_still_holding_does_not_trigger(con):
    did = _draft(con, invalidation_checks=[{"check": "drawdown_from",
                                            "reference": 900.0, "pct": 0.15}])
    ev = monitor.evaluate(con, did, date(2026, 9, 18))       # 850 is -5.6%, not -15%
    assert ev["triggered"] == [] and ev["results"][0]["state"] == "holding"


def test_prose_without_a_typed_twin_is_reported_unmonitored_not_satisfied(con):
    did = _draft(con, invalidation_conditions=["falls 15% from entry",
                                               "management cuts guidance"])
    ev = monitor.evaluate(con, did, date(2026, 9, 18))
    assert ev["unmonitored"] == ["management cuts guidance"]


def test_a_trigger_becomes_dated_evidence_and_an_event(con):
    did = _draft(con)
    out = monitor.run(con, date(2026, 9, 18))
    assert out["checked"] == 1 and out["triggered"] == 1
    row = con.execute("""SELECT check_name, evidence_id FROM decision_alerts
                         WHERE decision_id = ?""", [did]).fetchone()
    assert row[0] == "drawdown_from"
    claim = con.execute("SELECT claim, source_tier FROM evidence WHERE evidence_id = ?",
                        [row[1]]).fetchone()
    assert "invalidation condition met" in claim[0] and claim[1] == 1
    note = con.execute("""SELECT note FROM decision_events WHERE decision_id = ?
                          ORDER BY seq DESC LIMIT 1""", [did]).fetchone()[0]
    assert "invalidation: drawdown_from" in note


def test_the_same_trigger_is_not_raised_twice(con):
    _draft(con)
    monitor.run(con, date(2026, 9, 18))
    again = monitor.run(con, date(2026, 9, 18))
    assert again["triggered"] == 0, "same condition, same facts, same day"
    assert con.execute("SELECT COUNT(*) FROM decision_alerts").fetchone()[0] == 1


def test_a_closed_decision_is_not_monitored(con):
    did = _draft(con)
    dr.close(con, did, to="WITHDRAWN", actor="human:owner", note="changed my mind")
    assert monitor.live_decisions(con) == []
    assert monitor.run(con, date(2026, 9, 18))["checked"] == 0


def test_an_unknown_or_malformed_check_is_a_finding_not_a_crash(con):
    did = _draft(con, invalidation_checks=[{"check": "moon_phase"},
                                           {"check": "price_below"}])  # no level
    ev = monitor.evaluate(con, did, date(2026, 9, 18))
    assert [r["state"] for r in ev["results"]] == ["unknown_check", "error"]
    assert ev["triggered"] == []


def test_an_adverse_reading_only_counts_when_the_model_cleared_its_bar(con):
    con.execute("""INSERT INTO announcements (news_id, source, business_date, event_type,
                   materiality, evidence_key, observed_at, isin, headline) VALUES
                   ('n1','BSE',DATE '2026-09-17','LEGAL_REGULATORY','high','k',NOW(),
                    'INE000A01001','tax demand order')""")
    con.execute("""INSERT INTO announcement_tone (news_id, tone, confidence, model,
                   accepted, classified_at) VALUES ('n1','negative',0.6,'m',FALSE,NOW())""")
    did = _draft(con, invalidation_checks=[{"check": "adverse_tone",
                                            "since": "2026-09-01"}])
    assert monitor.evaluate(con, did, date(2026, 9, 18))["triggered"] == []
    con.execute("UPDATE announcement_tone SET accepted = TRUE WHERE news_id = 'n1'")
    ev = monitor.evaluate(con, did, date(2026, 9, 18))
    assert ev["triggered"] and "tax demand order" in ev["triggered"][0]["detail"]
