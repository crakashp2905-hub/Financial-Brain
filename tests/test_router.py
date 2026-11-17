from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from financial_brain.execution.exchange_paper import Account, ExchangePaperOms, PaperOrder
from financial_brain.paper.router import (
    FuturesSpec,
    Position,
    RiskClock,
    RouteError,
    Tradability,
    delta_exposure,
    etf_style_allowed,
    etf_units,
    expiry_guard,
    futures_lots,
    premium_exits,
    route,
    tradable,
)

T = datetime(2026, 11, 17, 4, 0, tzinfo=timezone.utc)


def base():
    return PaperOrder("O", "S", Account.AUTONOMOUS, "ETF", "IN:E", "BUY", 10, D(100), T + timedelta(hours=1))


def test_route_autonomous_always_and_approved_separately():
    m = ExchangePaperOms(max_quote_age=timedelta(seconds=5))
    out = route(m, base(), approved=True)
    assert [o.account for o in out] == [Account.AUTONOMOUS, Account.TRADER_APPROVED]
    m2 = ExchangePaperOms(max_quote_age=timedelta(seconds=5))
    assert len(route(m2, base(), approved=False)) == 1


def test_etf_style_and_tradability():
    assert etf_style_allowed("LONG") and not etf_style_allowed("SHORT")
    assert tradable(Tradability(False, True, True)) == "RESEARCH_ONLY"
    assert tradable(Tradability(True, False, True)) == "NO_TRADE"
    assert tradable(Tradability(True, True, True)) == "TRADABLE"


def test_premium_exits_via_delta_not_index_points():
    sl, tp = premium_exits(100.0, 0.5, 24000, 23960, 24080)
    assert sl == pytest.approx(80.0) and tp == pytest.approx(140.0)
    with pytest.raises(RouteError):
        premium_exits(100.0, 0.0, 24000, 23960, 24080)
    with pytest.raises(RouteError):
        premium_exits(10.0, 0.5, 24000, 23960, 24000)


def test_futures_and_etf_sizing_respect_margin_and_cash():
    assert futures_lots(10000, 24000, 23950, FuturesSpec(75, 0.12), cash=500000) == 2
    assert futures_lots(10000, 24000, 23950, FuturesSpec(75, 0.12), cash=100000) == 0
    assert etf_units(1000, 250, 245, cash=10000) == 40


def test_delta_exposure_and_uncovered():
    exp, unc = delta_exposure([Position("NIFTY", "ETF", 10, 250), Position("NIFTY", "OPTION", 75, 24000, 0.5),
                               Position("NIFTY", "MF", 100, 50, 1.0, lookthrough=False)])
    assert exp == {"NIFTY": 2500 + 900000} and unc == ["MF:NIFTY"]


def test_expiry_guard():
    d = date(2026, 11, 17)
    assert expiry_guard(date(2026, 11, 18), d, min_days=2, legs_synced=True, margin_modelled=True,
                        structure="LONG") == "too close to expiry"
    assert expiry_guard(date(2026, 11, 24), d, min_days=2, legs_synced=False, margin_modelled=True,
                        structure="DEBIT_SPREAD") is not None
    assert expiry_guard(date(2026, 11, 24), d, min_days=2, legs_synced=True, margin_modelled=True,
                        structure="LONG") is None


def test_risk_clock_checks_every_tick_signals_every_15m():
    c = RiskClock(timedelta(minutes=15))
    for i in range(60):
        c.on_tick(T + timedelta(minutes=i))
    assert c.checks == 60 and c.signals == 4
