from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.forward_edge.option_surface import (
    OptionSurfaceError,
    OptionSurfaceQuote,
    OptionSurfaceRequest,
    build_option_surface,
)
from financial_brain.kernel.options import bsm_price


AS_OF = datetime(2026, 11, 24, 5, 0, tzinfo=timezone.utc)
EXPIRY = AS_OF + timedelta(days=30)
SOURCE_SHA = "a" * 64


def _quote(strike: float, *, call=True, price=None, observed_at=AS_OF, available_at=AS_OF,
           bid_qty=10, ask_qty=10, spread=0.001):
    t = (EXPIRY - AS_OF).total_seconds() / (365 * 24 * 60 * 60)
    mid = price if price is not None else bsm_price(100, strike, t, 0.05, 0.25, call)
    return OptionSurfaceQuote(
        f"q-{strike}-{'C' if call else 'P'}", "KITE_FIXTURE", SOURCE_SHA, "NSE:NIFTY",
        f"NFO:NIFTY-{strike}-{'CE' if call else 'PE'}", EXPIRY, strike,
        "CE" if call else "PE", observed_at, available_at,
        mid - spread / 2, mid + spread / 2, bid_qty, ask_qty,
    )


def _request(quotes=None):
    return OptionSurfaceRequest("NSE:NIFTY", 100, 0.05, 0, AS_OF, 2, 100,
                                tuple(quotes or (_quote(90), _quote(100), _quote(110))))


def test_surface_snapshot_has_iv_greeks_source_hash_and_static_arbitrage_checks():
    result = build_option_surface(_request())
    assert result["quality_gate"] == "PASS"
    assert result["calendar_spread_arbitrage"] == "NOT_TESTED"
    assert result["arbitrage_checks"][0]["call_monotonicity"] == "PASS"
    assert result["arbitrage_checks"][0]["butterfly_convexity"] == "PASS"
    assert len(result["snapshot_sha256"]) == 64
    assert len(result["points"]) == 3
    assert all(0 < point["implied_volatility"] < 5 for point in result["points"])
    assert all(point["source_sha256"] == SOURCE_SHA for point in result["points"])


def test_surface_rejects_stale_future_and_wide_quotes():
    stale = _quote(90, observed_at=AS_OF - timedelta(seconds=3),
                   available_at=AS_OF - timedelta(seconds=3))
    with pytest.raises(OptionSurfaceError, match="stale"):
        build_option_surface(_request((stale, _quote(100), _quote(110))))
    future = _quote(90, available_at=AS_OF + timedelta(seconds=1))
    with pytest.raises(OptionSurfaceError, match="unavailable"):
        build_option_surface(_request((future, _quote(100), _quote(110))))
    wide = _quote(90, spread=2)
    with pytest.raises(OptionSurfaceError, match="spread"):
        build_option_surface(_request((wide, _quote(100), _quote(110))))


def test_surface_rejects_duplicate_or_cross_underlying_quotes():
    quotes = (_quote(90), replace(_quote(90), quote_id="q-duplicate"), _quote(110))
    with pytest.raises(OptionSurfaceError, match="duplicate option side"):
        build_option_surface(_request(quotes))
    wrong = replace(_quote(90), underlying_id="NSE:BANKNIFTY")
    with pytest.raises(OptionSurfaceError, match="underlying"):
        build_option_surface(_request((wrong, _quote(100), _quote(110))))


def test_surface_rejects_call_price_butterfly_violation():
    quotes = (_quote(90, price=20), _quote(100, price=17), _quote(110, price=11))
    with pytest.raises(OptionSurfaceError, match="convex"):
        build_option_surface(_request(quotes))


def test_surface_accepts_put_quotes_through_parity_conversion():
    puts = tuple(_quote(strike, call=False) for strike in (90, 100, 110))
    result = build_option_surface(_request(puts))
    assert len(result["points"]) == 3
    assert result["arbitrage_checks"][0]["call_monotonicity"] == "PASS"
