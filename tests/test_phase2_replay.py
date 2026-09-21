"""Replaying the committee over past sessions (C23/C25).

The scorecard cannot judge decisions that were never made, and waiting a year for live
calls is the slowest honest route. These tests pin what keeps a replay from becoming a
fiction: the universe is what that session actually surfaced, a session with no prices is
skipped rather than invented, and one company failing does not take the session with it.
"""
from __future__ import annotations

from datetime import date

import pytest

from financial_brain.config import Config
from financial_brain.evaluation import replay
from financial_brain.storage.db import Database


@pytest.fixture
def con(tmp_path):
    db = Database(Config(data_root=tmp_path).ensure())
    db.migrate()
    with db.connect() as c:
        c.execute("""CREATE OR REPLACE VIEW adjusted_prices AS SELECT * FROM (VALUES
            ('L1', DATE '2026-03-16', CAST(100.0 AS DOUBLE)))
            t(lineage, business_date, close_adj)""")
        c.execute("""CREATE OR REPLACE VIEW security_lineage AS
                     SELECT 'INE000A01001' AS isin, 'L1' AS lineage""")
        yield c


def _filing(con, isin, event, on, nid, materiality="high"):
    con.execute("""INSERT INTO announcements (news_id, source, business_date, event_type,
                   materiality, evidence_key, observed_at, isin, company, headline)
                   VALUES (?, 'BSE', ?, ?, ?, ?, NOW(), ?, 'Acme', 'x')""",
                [nid, on, event, materiality, f"k{nid}", isin])


def test_the_universe_is_what_that_session_surfaced(con):
    _filing(con, "INE000A01001", "ORDER_WIN", date(2026, 3, 16), "n1")
    _filing(con, "INE000A01001", "RESULTS", date(2026, 3, 16), "n2")
    _filing(con, "INE999Z01001", "ORDER_WIN", date(2026, 3, 16), "n3")   # no price series
    _filing(con, "INE000A01001", "ORDER_WIN", date(2026, 2, 2), "n4")    # another session
    got = replay.candidates(con, date(2026, 3, 16), limit=5)
    assert got == [("INE000A01001", "Acme")], "priced companies filing that day, only"


def test_low_materiality_filings_do_not_summon_a_committee(con):
    _filing(con, "INE000A01001", "ORDER_WIN", date(2026, 3, 16), "n1", materiality="low")
    assert replay.candidates(con, date(2026, 3, 16), limit=5) == []


def test_a_session_with_no_prices_produces_no_candidates(con):
    """2026-01-15 had 591 filings and no trading - Makar Sankranti. A replay must skip
    it rather than enter a trade at a price that never existed."""
    _filing(con, "INE000A01001", "ORDER_WIN", date(2026, 1, 15), "n1")
    assert replay.candidates(con, date(2026, 1, 15), limit=5) == []


def test_the_busiest_companies_come_first(con):
    _filing(con, "INE000A01001", "ORDER_WIN", date(2026, 3, 16), "n1")
    _filing(con, "INE000A01001", "RESULTS", date(2026, 3, 16), "n2")
    con.execute("""CREATE OR REPLACE VIEW security_lineage AS
                   SELECT 'INE000A01001' AS isin, 'L1' AS lineage UNION ALL
                   SELECT 'INE111B01001', 'L1'""")
    _filing(con, "INE111B01001", "RESULTS", date(2026, 3, 16), "n3")
    got = replay.candidates(con, date(2026, 3, 16), limit=1)
    assert got == [("INE000A01001", "Acme")], "two filings beat one"


def test_one_company_failing_does_not_end_the_session(con, monkeypatch):
    _filing(con, "INE000A01001", "ORDER_WIN", date(2026, 3, 16), "n1")
    monkeypatch.setattr(replay.ws, "build",
                        lambda c, d: {"version_id": "ws_1", "as_of": str(d)})

    def boom(*a, **k):
        raise RuntimeError("ollama is down")

    monkeypatch.setattr(replay.committee, "convene", boom)
    out = replay.session(con, date(2026, 3, 16), per_day=2)
    assert out["considered"] == 1 and out["drafted"] == 0
    assert "committee failed: RuntimeError" in out["notes"][0]
