"""The control a replayed record needs (C25).

The committee only ever sees companies that filed something material that session. That
universe has its own return, so without pricing it a positive result gets claimed for the
committee when it belonged to the market, and a negative one gets blamed on the committee
when the universe was falling.
"""
from __future__ import annotations

from datetime import date

import pytest

from financial_brain.config import Config
from financial_brain.evaluation import control
from financial_brain.storage.db import Database


@pytest.fixture
def con(tmp_path):
    db = Database(Config(data_root=tmp_path).ensure())
    db.migrate()
    with db.connect() as c:
        c.execute("""CREATE OR REPLACE VIEW adjusted_prices AS SELECT * FROM (VALUES
            ('L1', DATE '2026-03-02', CAST(100.0 AS DOUBLE)),
            ('L1', DATE '2026-06-01', CAST(120.0 AS DOUBLE)),
            ('L2', DATE '2026-03-02', CAST(50.0 AS DOUBLE)),
            ('L2', DATE '2026-06-01', CAST(40.0 AS DOUBLE)))
            t(lineage, business_date, close_adj)""")
        c.execute("""CREATE OR REPLACE VIEW security_lineage AS
                     SELECT 'INE000A01001' AS isin, 'L1' AS lineage UNION ALL
                     SELECT 'INE111B01001', 'L2'""")
        yield c


def test_a_blind_buy_is_priced_the_same_way_a_trade_is(con):
    got = control.outcome(con, "INE000A01001", date(2026, 3, 2), cost=0.004)
    assert got["entry_date"] == date(2026, 3, 2) and got["exit_date"] == date(2026, 6, 1)
    assert got["stock_return"] == pytest.approx(0.20)
    # no index rows in this fixture, so the benchmark is zero and cost still applies
    assert got["excess"] == pytest.approx(0.196)


def test_a_company_with_no_price_is_skipped_not_counted_as_zero(con):
    assert control.outcome(con, "INE999Z01999", date(2026, 3, 2)) is None


def test_the_comparison_separates_the_committee_from_its_universe(con):
    control.record_candidates(con, date(2026, 3, 2),
                              [("INE000A01001", "Winner"), ("INE111B01001", "Loser")],
                              drafted={"INE000A01001"})
    con.execute("""INSERT INTO paper_trades (decision_id, isin, lineage, action, direction,
        entry_date, entry_price, due_date, cost, status, excess, opened_at)
        VALUES ('d1','INE000A01001','L1','BUY',1,DATE '2026-03-02',100.0,
                DATE '2026-06-01',0.004,'closed',0.196,NOW())""")
    result = control.compare(con)
    assert result["selected"]["n"] == 1 and result["control"]["n"] == 2
    # the committee picked the winner out of a universe holding one of each
    assert result["contribution"] > 0
    text = " ".join(control.lines(result))
    assert "Committee contribution" in text
    assert "cannot separate judgement from luck" in text


def test_nothing_to_compare_says_so(con):
    assert "Not enough of a record" in " ".join(control.lines(control.compare(con)))
