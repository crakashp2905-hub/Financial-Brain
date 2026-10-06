"""The intraday data plane and execution intelligence: polls archived, bars rebuilt, VWAP and book-walk recorded."""
from datetime import datetime

import pytest

from financial_brain.ingest import quote_archive as QA
from financial_brain.paper import execution as X
from financial_brain.paper import intraday as I
from financial_brain.paper import intraday_runner as R


def q(ltp, vol, vwap=100.0):
    return {"NSE:ABC": {"last_price": ltp, "average_price": vwap, "volume": vol, "oi": 0,
                        "ohlc": {"open": 100.0, "high": 102.0, "low": 99.0, "close": 99.5},
                        "depth": {"buy": [{"price": ltp - 0.05, "quantity": 100}], "sell": [{"price": ltp + 0.05, "quantity": 50},
                                                                                           {"price": ltp + 0.10, "quantity": 500}]}}}


def test_polls_are_archived_and_rebuilt_into_bars(tmp_path):
    con = QA.connect(tmp_path / "q.duckdb")
    for k, (px, vol) in enumerate([(100, 1000), (101, 1500), (100.5, 1700), (102, 3000)]):
        QA.store(con, datetime(2026, 10, 7, 9, 15 + 2 * k), q(px, vol))
    b = QA.bars(con, "ABC", "2026-10-07", minutes=5)
    assert list(b["open"]) == [100, 102] and b["high"].iloc[0] == 101 and b["volume"].iloc[1] == 1300
    br = QA.breadth(con, "2026-10-07")
    assert len(br) == 4 and set(br["names"]) == {1}


def test_walking_the_book_costs_more_than_the_touch():
    assert I.DayEngine.walk_cost_bp([(100.05, 50), (100.10, 500)], 50) == pytest.approx(0.0)
    assert I.DayEngine.walk_cost_bp([(100.05, 50), (100.10, 500)], 150) == pytest.approx((100.05 * 50 + 100.10 * 100) / 150 / 100.05 * 1e4 - 1e4)
    assert I.DayEngine.walk_cost_bp([(100.05, 50)], 500) is None          # too thin to see the whole fill


def test_vwap_and_book_walk_are_recorded_on_the_trade_without_changing_the_fill(tmp_path):
    e = I.DayEngine("2026-10-07")
    e.add_symbol("ABC", 99.5)
    e.set_opening("ABC", open_px=100.0, or_hi=101.0, or_lo=99.0)
    e.set_market("ABC", vwap=100.4, depth_buy=[(101.4, 100)], depth_sell=[(101.6, 10), (101.7, 10_000)])
    t = next(x for x in e.on_price("ABC", datetime(2026, 10, 7, 9, 35), 101.5, bid=101.4, ask=101.6) if x.strategy == "ORB")
    assert t.entry_px == pytest.approx(101.6) and t.entry_vwap == 100.4 and t.entry_walk_bp > 0
    con = R.ledger(tmp_path / "l.duckdb")
    for x in e.end_of_day(datetime(2026, 10, 7, 15, 20), {"ABC": 102.0}):
        R.save(con, x, "live")
    s = X.shortfall(con)[0]
    assert s["entry_vs_vwap_bp"] == pytest.approx((101.6 / 100.4 - 1) * 1e4) and s["with_depth"] == 1
