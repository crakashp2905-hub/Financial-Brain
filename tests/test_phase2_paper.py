"""Paper trading: fills only at prices the decision could have had; costs and the
benchmark are applied once, by rule."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import pytest

from financial_brain.config import Config
from financial_brain.decisions import record as dr
from financial_brain.evidence import ledger
from financial_brain.paper import ledger as paper
from financial_brain.storage.db import Database

ISIN = "INE092A01019"
D0 = date(2026, 1, 1)


@pytest.fixture
def con(tmp_path):
    d = Database(Config(data_root=tmp_path).ensure())
    d.migrate()
    with d.connect() as c:
        yield c


def _market(con, days=80):
    for i in range(days):
        d = D0 + timedelta(days=i)
        con.execute("""INSERT INTO universe_snapshots (business_date, isin, exchange, ticker,
            series, instrument_type, turnover, close_price, tradable)
            VALUES (?, ?, 'NSE', 'TATACHEM', 'EQ', 'STK', 2e9, ?, TRUE)""",
                    [d, ISIN, 100.0 + i])                      # +1 a day
        con.execute("""INSERT INTO index_levels (business_date, index_name, close_level,
            variant, source, observed_at) VALUES (?, 'Nifty 50', ?, 'PRICE', 'NSE', NOW())""",
                    [d, 1000.0])                                # flat benchmark
    from financial_brain.features import indicators
    indicators.build(con)


def _decision(con, action="BUY", as_of=datetime(2026, 1, 11, 9, 0)):
    con.execute("INSERT INTO world_states VALUES ('ws', DATE '2026-01-10', NOW(), ?, 0, 't')",
                [json.dumps({"as_of": as_of.isoformat()})])
    ev = [ledger.mint(con, kind="announcement", subject=ISIN, as_of=datetime(2026, 1, 10, 16),
                      published_at=datetime(2026, 1, 10, 16), claim=c, value={"c": c},
                      source="BSE", source_tier=1, derivation="test") for c in ("a", "b")]
    did = dr.draft(con, dr.Decision(
        isin=ISIN, action=action, horizon_days=30, thesis="t", world_state_version="ws",
        supporting_evidence=[ev[0]], contrary_evidence=[ev[1]], primary_uncertainty="u",
        invalidation_conditions=["x"], sizing={"weight": 0.02}))
    for _ in range(3):
        dr.advance(con, did, actor="agent:committee", constitution={})
    return did


def test_entry_is_the_first_close_after_the_as_of_and_exit_after_the_horizon(con):
    _market(con)
    did = _decision(con)
    t = paper.open_trade(con, did)
    assert t["entry_date"] == date(2026, 1, 11) and t["entry_price"] == pytest.approx(110.0)
    assert paper.mark(con) == {"closed": 1, "open": 0}
    r = con.execute("SELECT exit_date, stock_return, nifty_return, excess, cost "
                    "FROM paper_trades").fetchone()
    assert r[0] == date(2026, 2, 10)
    assert r[1] == pytest.approx(140 / 110 - 1) and r[2] == pytest.approx(0.0)
    assert r[3] == pytest.approx(r[1] - r[4]), "excess = stock - nifty - costs"


def test_an_avoid_is_right_when_the_stock_rises_less_than_nothing(con):
    _market(con)
    did = _decision(con, action="AVOID")
    paper.open_trade(con, did)
    paper.mark(con)
    excess = con.execute("SELECT excess FROM paper_trades").fetchone()[0]
    assert excess < 0, "avoiding a stock that rose 27% was wrong"


def test_not_before_paper_candidate_and_not_twice(con):
    _market(con)
    did = _decision(con)
    assert paper.open_trade(con, did)["status"] == "open"
    assert paper.open_trade(con, did)["status"] == "exists"
    con.execute("DELETE FROM decision_events WHERE seq > 1")
    con.execute("DELETE FROM paper_trades")
    with pytest.raises(ValueError, match="PAPER_CANDIDATE"):
        paper.open_trade(con, did)


def test_a_trade_stays_open_until_its_exit_has_a_price(con):
    _market(con, days=30)
    did = _decision(con)
    paper.open_trade(con, did)
    assert paper.mark(con) == {"closed": 0, "open": 1}


def test_a_split_during_the_trade_does_not_look_like_a_crash(con):
    """A 1:2 split mid-trade: raw closes halve, the paper result must not."""
    _market(con)
    did = _decision(con)
    paper.open_trade(con, did)
    con.execute("""UPDATE universe_snapshots SET close_price = close_price / 2
                   WHERE business_date >= DATE '2026-01-25'""")
    con.execute("""INSERT INTO adjustment_factors (isin, effective_from, price_factor,
                   volume_factor, derived_from, computed_at)
                   VALUES (?, DATE '2026-01-25', 0.5, 2.0, 'test', NOW())""", [ISIN])
    from financial_brain.features import indicators
    indicators.build(con)                          # rebases every adjusted close
    paper.mark(con)
    stock = con.execute("SELECT stock_return FROM paper_trades").fetchone()[0]
    assert stock == pytest.approx(140 / 110 - 1)
