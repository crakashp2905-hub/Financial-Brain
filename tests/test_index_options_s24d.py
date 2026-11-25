from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest

from financial_brain.signals.index_options import (
    Contract,
    IndexSignalState,
    choose,
    expected_option_pnl,
)

NOW = datetime(2026, 11, 17, 4, 0, tzinfo=timezone.utc)
CE = Contract("T1", "NIFTY", date(2026, 11, 24), 24000, "CE", 75, 100.0, 100.5, 1000, 1000,
              NOW - timedelta(seconds=2), 0.5, 0.001, -10.0, 5.0)
MOVES_UP = [(0.6, 60.0), (0.4, -30.0)]
ARGS = dict(max_age=timedelta(seconds=10), horizon_days=15 / 375, iv_change=0.0, fees_per_lot=40.0)


def test_expected_pnl_formula():
    ev = expected_option_pnl(CE, MOVES_UP, 0.0, 0.0, 0.0)
    move = 0.6 * (30 + 0.5 * 0.001 * 3600) + 0.4 * (-15 + 0.5 * 0.001 * 900)
    assert ev == pytest.approx((move - 0.5) * 75)


def test_bullish_forecast_with_cheap_option_is_eligible():
    c = choose("NIFTY", 0.6, 0.3, MOVES_UP, [CE], {"T1"}, NOW, **ARGS)
    assert c.state is IndexSignalState.PAPER_ELIGIBLE and c.token == "T1"


def test_bullish_forecast_with_expensive_option_is_no_trade():
    pricey = replace(CE, bid=95.0, ask=110.0, theta_per_day=-400.0)
    c = choose("NIFTY", 0.65, 0.2, MOVES_UP, [pricey], {"T1"}, NOW, **ARGS, )
    assert c.state is IndexSignalState.NO_TRADE and c.expected_net_per_lot < 0


def test_iv_crush_turns_trade_off():
    c = choose("NIFTY", 0.6, 0.3, MOVES_UP, [CE], {"T1"}, NOW,
               max_age=timedelta(seconds=10), horizon_days=0.04, iv_change=-5.0, fees_per_lot=40.0)
    assert c.state is IndexSignalState.NO_TRADE


@pytest.mark.parametrize("bad,reason", [
    (dict(token="X"), "listed"), (dict(expiry=date(2026, 11, 10)), "expired"),
    (dict(bid=0.0), "two-sided"), (dict(quote_at=NOW - timedelta(minutes=5)), "stale"),
    (dict(ask_qty=10), "depth"),
])
def test_unusable_contracts_never_trade(bad, reason):
    c = choose("NIFTY", 0.6, 0.3, MOVES_UP, [replace(CE, **bad)], {"T1"}, NOW, **ARGS)
    assert c.state is IndexSignalState.UNPRICED and reason in c.reason


def test_non_optionable_index_is_research_only():
    assert choose("NIFTYIT", 0.9, 0.1, MOVES_UP, [CE], {"T1"}, NOW, **ARGS).state \
        is IndexSignalState.RESEARCH_ONLY


def test_no_lean_is_no_trade():
    assert choose("NIFTY", 0.4, 0.35, MOVES_UP, [CE], {"T1"}, NOW, **ARGS).state \
        is IndexSignalState.NO_TRADE
