"""A live thesis, and anything that broke it, belongs in the brief.

Phase 2's exit test is a thesis whose invalidation conditions are *actively monitored*.
Monitoring that only a database sees is not monitoring, so the brief carries each live
decision, any fired condition (cited), and an honest count of conditions nothing checks.
"""
from __future__ import annotations

from datetime import date

import pytest

from financial_brain.brief.render import render
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
            ('INE000A01001', DATE '2026-09-18', CAST(850.0 AS DOUBLE)))
            t(isin, business_date, close_price)""")
        yield c


def _state(theses):
    return {"business_date": date(2026, 9, 18), "as_of": "2026-09-21 09:00:00",
            "version_id": "ws_test", "market": None, "indices": [], "sectors": [],
            "gainers": [], "losers": [], "events": [], "upcoming": [], "quality": [],
            "theses": theses}


def test_a_fired_condition_appears_in_the_brief_with_its_citation(con):
    did = dr.draft(con, dr.Decision(
        isin="INE000A01001", action="BUY", horizon_days=90, thesis="cheap for the growth",
        world_state_version="ws_test", supporting_evidence=["ev_a"],
        contrary_evidence=["ev_b"], primary_uncertainty="margins",
        invalidation_conditions=["falls 15% from entry", "management cuts guidance"],
        invalidation_checks=[{"check": "drawdown_from", "reference": 1000.0, "pct": 0.15}]))
    out = monitor.run(con, date(2026, 9, 18))
    alert = con.execute("""SELECT check_name, detail, as_of, evidence_id
                           FROM decision_alerts""").fetchone()
    md, cited = render(con, _state([{
        "decision_id": did, "isin": "INE000A01001", "action": "BUY",
        "thesis": "cheap for the growth", "state": "DRAFT",
        "alerts": [{"check": alert[0], "detail": alert[1], "as_of": alert[2],
                    "evidence": alert[3]}],
        "unmonitored": 1}]))
    assert out["triggered"] == 1
    assert "## Theses under watch" in md
    assert "**invalidated** (drawdown_from)" in md
    assert alert[3] in cited, "the alert must be citable, like every other claim"
    assert "1 stated condition(s) have no automatic check" in md


def test_a_thesis_that_still_holds_says_so(con):
    md, _ = render(con, _state([{"decision_id": "dc_1", "isin": "INE000A01001",
                                 "action": "BUY", "thesis": "t", "state": "EVIDENCE_VERIFIED",
                                 "alerts": [], "unmonitored": 0}]))
    assert "no invalidation condition has fired" in md
    assert "have no automatic check" not in md


def test_no_live_thesis_means_no_section(con):
    md, _ = render(con, _state([]))
    assert "Theses under watch" not in md
