from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from financial_brain.features.etf_liquidity import (
    ETFDailyLiquidityObservation,
    analyze_etf_liquidity,
)
from financial_brain.forward_edge.market_data import normalize_kite_quote


UTC = timezone.utc
DAYS = [date(2026, 11, 10), date(2026, 11, 11), date(2026, 11, 12)]


def _daily() -> list[ETFDailyLiquidityObservation]:
    return [
        ETFDailyLiquidityObservation(day, close, turnover, f"{i:x}" * 64)
        for i, (day, close, turnover) in enumerate(zip(DAYS, (100.0, 101.0, 102.0),
                                                       (100_000.0, 200_000.0, 100_000.0), strict=True), 1)
    ]


def _quote(*, received_delay: int = 1):
    event_time = datetime(2026, 11, 12, 4, 45, tzinfo=UTC)
    return normalize_kite_quote(
        "BSE:INDEXETF",
        {"timestamp": "2026-11-12 10:15:00", "last_price": 100, "volume": 100,
         "depth": {"buy": [{"price": 99.9, "quantity": 12}],
                   "sell": [{"price": 100.1, "quantity": 10}]}},
        received_at=event_time + timedelta(seconds=received_delay),
    )


def _analyze(*, daily=None, quotes=None, expected=None):
    return analyze_etf_liquidity(
        daily if daily is not None else _daily(),
        quotes if quotes is not None else [_quote()],
        expected_sessions=expected if expected is not None else DAYS,
        venue="BSE", instrument_id="BSE:INDEXETF", intended_notional_inr=1000,
        max_quote_age=timedelta(seconds=2), max_session_gap_fraction=0.0,
        max_stale_quote_fraction=0.5, max_participation_fraction=0.02,
        max_estimated_all_in_cost_bps=100, other_round_trip_cost_bps=5,
    )


def test_liquidity_report_combines_spread_depth_staleness_gaps_and_size_cost() -> None:
    report = _analyze(quotes=[_quote(), _quote(received_delay=10)])
    assert report["status"] == "ESTIMATE"
    assert report["median_daily_turnover_inr"] == 100_000
    assert report["median_spread_bps"] > 0
    assert report["median_top_bid_depth_inr"] == pytest.approx(1198.8)
    assert report["median_top_ask_depth_inr"] == pytest.approx(1001.0)
    assert report["stale_or_non_executable_quote_fraction"] == 0.5
    assert report["capacity_participation_fraction"] == pytest.approx(0.01)
    assert report["estimated_all_in_round_trip_cost_bps"] > report["other_round_trip_cost_bps"]
    assert report["estimated_all_in_round_trip_cost_bps"] == pytest.approx(
        report["median_spread_bps"] + report["amihud_size_impact_proxy_bps"]
        + report["other_round_trip_cost_bps"]
    )
    assert "full observed spread" in report["cost_model_label"]
    assert report["limits_met"] is True


def test_missing_sessions_are_reported_and_sparse_history_abstains() -> None:
    report = _analyze(daily=_daily()[:1])
    assert report["missing_sessions"] == [day.isoformat() for day in DAYS[1:]]
    assert report["status"] == "INSUFFICIENT_DATA"
    assert report["limits_met"] is False
    assert report["estimated_all_in_round_trip_cost_bps"] is None


def test_duplicate_daily_revision_must_be_resolved_before_liquidity_analysis() -> None:
    daily = _daily()
    with pytest.raises(ValueError, match="duplicate ETF daily"):
        _analyze(daily=daily + [daily[-1]])
