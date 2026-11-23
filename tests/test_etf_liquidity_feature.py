from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from financial_brain.etf_history import ETFPriceBar
from financial_brain.etf_liquidity import report, sweep_depth
from financial_brain.forward_edge.market_events import EventKind, MarketEvent


def _quote(at: datetime, *, status: str = "OPEN") -> MarketEvent:
    return MarketEvent(
        event_id=at.isoformat(), kind=EventKind.QUOTE, venue="NSE", instrument_id="NSE:ETFONE",
        event_time=at, received_at=at + timedelta(seconds=1),
        available_at=at + timedelta(seconds=1), source="KITE_CONNECT_READ_ONLY",
        payload={
            "bid": 99.9, "ask": 100.1, "volume": 1000, "exchange_status": status,
            "depth": {"buy": [{"price": 99.9, "quantity": 100}],
                      "sell": [{"price": 100.1, "quantity": 100}]},
        },
    )


def _bars() -> list[ETFPriceBar]:
    return [
        ETFPriceBar(date(2026, 11, 19), "INF000A01010", "ETFONE", 98, 100, 97, 99,
                    1000, 100_000, 1.0, "raw-19"),
        ETFPriceBar(date(2026, 11, 20), "INF000A01010", "ETFONE", 99, 101, 98, 100,
                    1100, 110_000, 1.0, "raw-20"),
        ETFPriceBar(date(2026, 11, 23), "INF000A01010", "ETFONE", 100, 102, 99, 101,
                    900, 90_000, 1.0, "raw-23"),
    ]


def test_depth_sweep_never_fills_beyond_displayed_size() -> None:
    result = sweep_depth([{"price": 100.1, "quantity": 40}], units=50,
                         side="buy", reference_mid=100)
    assert result.filled_units == 40 and result.residual_units == 10
    assert result.vwap == pytest.approx(100.1)
    assert not result.complete


def test_etf_liquidity_report_exposes_cost_capacity_and_data_gaps() -> None:
    t0 = datetime(2026, 11, 23, 9, 15, tzinfo=timezone.utc)
    events = [_quote(t0), _quote(t0 + timedelta(minutes=2), status="HALTED")]
    result = report(
        events=events, bars=_bars(), intended_units=150,
        expected_sessions=[date(2026, 11, 19), date(2026, 11, 20),
                           date(2026, 11, 21), date(2026, 11, 23)],
        expected_poll_seconds=60,
    )
    assert result["quoted_spread_median_bps"] == pytest.approx(20)
    assert result["effective_spread_bps"] is None
    assert result["effective_spread_reason"].startswith("TRADE_SIDE")
    assert result["stale_interval_count"] == 1
    assert result["halted_quote_fraction"] == 0.5
    assert result["median_daily_traded_value"] == 100_000
    assert result["missing_expected_sessions"] == ["2026-11-21"]
    assert result["buy_depth_sweep"]["residual_units"] == 50
    assert result["buy_cost_model"] is None


def test_empty_liquidity_inputs_stay_unknown() -> None:
    result = report(events=[], bars=[], intended_units=10)
    assert result["quoted_spread_median_bps"] is None
    assert result["median_daily_traded_value"] is None
    assert result["buy_depth_sweep"] is None
    with pytest.raises(ValueError, match="positive"):
        report(events=[], bars=[], intended_units=0)
