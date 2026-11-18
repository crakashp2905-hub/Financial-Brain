from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from financial_brain.execution.option_book import (
    LegState,
    OptionContract,
    OptionError,
    OptionQuote,
    Spread,
    block_structure,
    contract_pnl,
    iv_checks,
    monitor,
    select,
)

T = datetime(2026, 11, 17, 4, 0, tzinfo=timezone.utc)


def c(token, strike, kind="CE", expiry=date(2026, 11, 24), lot=75):
    return OptionContract(token, "NIFTY", kind, strike, expiry, lot, date(2026, 11, 1))


def q(bid, ask, qty=1000, oi=None, theta=-5.0, at=T):
    return OptionQuote(bid, ask, qty, qty, oi, 0.15, theta, at)


def test_invalid_contract_refused():
    with pytest.raises(OptionError):
        OptionContract("X", "NIFTY", "XX", 1, date(2026, 11, 24), 75, date(2026, 11, 1))


def test_select_ranks_and_excludes_unusable():
    chain = [(c("A", 24000), q(100, 101)), (c("B", 24100), q(60, 62)),
             (c("Z", 24200), q(0, 5)), (c("S", 24300), q(40, 41, at=T - timedelta(minutes=5))),
             (c("E", 24000, expiry=date(2026, 11, 10)), q(100, 101)),
             (c("D", 24000), q(100, 101, qty=10))]
    r = select(chain, T, kind="CE", lots=1, max_age=timedelta(seconds=10), max_spread_pct=0.05, min_days=1)
    assert [t for t, _, _ in r] == ["A", "B"]
    assert r[0][2] == "OI unavailable"


def test_block_uncovered_writing():
    assert block_structure("DEBIT_CALL_SPREAD") is None
    assert block_structure("SHORT_STRADDLE") is not None


def test_spread_partial_leg_is_hedge_risk_and_combined_triggers():
    s = Spread([LegState(c("L", 24000), "BUY", 2), LegState(c("H", 24200), "SELL", 2)], 20.0, 120.0)
    s.legs[0].fill(2, 100)
    assert s.check({"L": 90, "H": 30}) == "HEDGE_RISK"
    s.legs[1].fill(2, 40)
    assert s.check({"L": 80, "H": 65}) == "STOP"
    assert s.check({"L": 200, "H": 70}) == "TARGET"
    assert s.close_orders() == [("L", "SELL", 2), ("H", "BUY", 2)]
    with pytest.raises(OptionError):
        s.legs[0].fill(1, 100)


def test_monitor_flags():
    f = monitor(c("A", 24000), q(10, 13, qty=50, theta=-2), T, max_spread_pct=0.1, min_depth_lots=1,
                min_days=10)
    assert set(f) == {"WIDE_SPREAD", "THIN_DEPTH", "NEAR_EXPIRY", "FAST_DECAY"}
    assert monitor(c("A", 24000), q(0, 1), T, max_spread_pct=1, min_depth_lots=0, min_days=0) == ["NO_BID"]


def test_iv_checks():
    assert iv_checks({100: 10, 105: 7, 110: 5}, 0.15, 0.16, 0.05, 0.1) == []
    p = iv_checks({100: 10, 105: 11, 110: 5}, 0.2, 0.1, 0.05, 0.06)
    assert "call price increases with strike" in p and "calendar total variance decreasing" in p
    assert any("convexity" in x for x in iv_checks({100: 10, 105: 9, 110: 2}, None, None, 0, 0))


def test_contract_pnl():
    r = contract_pnl([("BUY", 2, 100.0), ("SELL", 1, 120.0)], 75, 90.0, 40.0)
    assert r["realized"] == 20 * 75 - 40 and r["unrealized"] == (90 - 100) * 75 and r["open_lots"] == 1
    assert contract_pnl([("BUY", 1, 100.0)], 75, None, 0)["unrealized"] is None
    with pytest.raises(OptionError):
        contract_pnl([("SELL", 1, 1.0)], 75, 1, 0)
