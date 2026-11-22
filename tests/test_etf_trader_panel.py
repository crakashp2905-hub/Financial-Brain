from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from financial_brain.features.etf_trader_panel import build_etf_trader_panel
from financial_brain.forward_edge.market_data import normalize_kite_quote
from financial_brain.providers.etf_reference import (
    ETFReferenceError,
    ETFReferenceRecord,
    ETFTrackingMetrics,
    ETFValuationObservation,
)


UTC = timezone.utc
AS_OF = datetime(2026, 11, 13, 4, 45, 3, tzinfo=UTC)


def identity() -> ETFReferenceRecord:
    return ETFReferenceRecord(
        source_id="NSE", exchange="NSE", symbol="INDEXETF", isin="INE000A01001",
        listing_date=date(2020, 1, 2), effective_from=date(2020, 1, 2), effective_to=None,
        series="EQ", published_at=datetime(2020, 1, 2, 3, tzinfo=UTC),
        observed_at=datetime(2020, 1, 2, 4, tzinfo=UTC), source_ref="reference",
        source_sha256="a" * 64, underlying_index="NIFTY 50",
    )


def quote(symbol: str = "INDEXETF"):
    return normalize_kite_quote(
        f"NSE:{symbol}",
        {"timestamp": "2026-11-13 10:15:00", "last_price": 101.5, "volume": 100,
         "depth": {"buy": [{"price": 101.4, "quantity": 12}],
                   "sell": [{"price": 101.6, "quantity": 9}]}},
        received_at=AS_OF,
    )


def inav() -> ETFValuationObservation:
    return ETFValuationObservation(
        source_id="NSE", exchange="NSE", symbol="INDEXETF", isin="INE000A01001",
        valuation_type="INAV", valuation_date=date(2026, 11, 13), value_per_unit=Decimal("100"),
        published_at=AS_OF - timedelta(seconds=2), observed_at=AS_OF - timedelta(seconds=1),
        source_ref="exchange iNAV", source_sha256="b" * 64,
    )


def tracking() -> ETFTrackingMetrics:
    return ETFTrackingMetrics(
        source_id="AMC", exchange="NSE", symbol="INDEXETF", isin="INE000A01001",
        effective_from=date(2026, 1, 1), effective_to=None,
        tracking_difference_bps=Decimal("-35"), tracking_error_bps=Decimal("18"),
        expense_ratio_bps=Decimal("5"), published_at=AS_OF - timedelta(days=2),
        observed_at=AS_OF - timedelta(days=1), source_ref="monthly factsheet",
        source_sha256="c" * 64,
    )


def build(**overrides):
    fields = {
        "identity": identity(), "quote": quote(), "inav": inav(), "tracking": tracking(),
        "liquidity": {
            "status": "ESTIMATE", "instrument_id": "NSE:INDEXETF",
            "estimated_all_in_round_trip_cost_bps": 12.0, "median_spread_bps": 19.7,
            "capacity_participation_fraction": 0.02, "cost_model_label": "observed inputs; proxy",
            "source_sha256": ["d" * 64],
        },
        "as_of": AS_OF, "max_quote_age": timedelta(seconds=5),
        "max_inav_age": timedelta(seconds=10), "max_quote_inav_skew": timedelta(seconds=2),
        "max_tracking_age": timedelta(days=30),
    }
    fields.update(overrides)
    return build_etf_trader_panel(**fields)


def test_panel_displays_benchmark_quote_valuation_tracking_cost_and_liquidity():
    panel = build()
    assert panel["underlying_benchmark"] == "NIFTY 50"
    assert panel["quote"]["status"] == "FRESH"
    assert panel["quote"]["bid"] == "101.4"
    assert panel["quote"]["ask"] == "101.6"
    assert panel["valuation"]["premium_discount_bps"] == "150"
    assert panel["tracking"]["tracking_difference_bps"] == "-35"
    assert panel["tracking"]["expense_ratio_bps"] == "5"
    assert panel["liquidity_and_cost"]["estimated_all_in_round_trip_cost_bps"] == 12.0
    assert panel["liquidity_and_cost"]["source_sha256"] == ["d" * 64]
    assert panel["display_only"] is True


def test_panel_keeps_stale_quote_and_unavailable_inav_explicit():
    stale = quote()
    panel = build(as_of=AS_OF + timedelta(minutes=1), quote=stale)
    assert panel["quote"]["status"] == "STALE"
    assert panel["valuation"]["status"] == "UNAVAILABLE"
    assert panel["valuation"]["premium_discount_bps"] is None
    assert "stale" in panel["valuation"]["reason"]


def test_panel_refuses_cross_instrument_quote_and_liquidity_evidence():
    with pytest.raises(ETFReferenceError, match="same listed ETF"):
        build(quote=quote("OTHER"))
    wrong_liquidity = {
        "status": "ESTIMATE", "instrument_id": "NSE:OTHER", "source_sha256": [],
    }
    with pytest.raises(ETFReferenceError, match="same ETF instrument"):
        build(liquidity=wrong_liquidity)


def test_panel_hides_not_yet_effective_tracking_disclosure():
    future = tracking()
    future = ETFTrackingMetrics(
        source_id=future.source_id, exchange=future.exchange, symbol=future.symbol, isin=future.isin,
        effective_from=date(2027, 1, 1), effective_to=None,
        tracking_difference_bps=future.tracking_difference_bps,
        tracking_error_bps=future.tracking_error_bps, expense_ratio_bps=future.expense_ratio_bps,
        published_at=future.published_at, observed_at=future.observed_at,
        source_ref=future.source_ref, source_sha256=future.source_sha256,
    )
    panel = build(tracking=future)
    assert panel["tracking"]["status"] == "OUT_OF_EFFECTIVE_RANGE"
    assert panel["tracking"]["tracking_difference_bps"] is None

