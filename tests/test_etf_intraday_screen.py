from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from financial_brain.features.etf_intraday_screen import (
    ETFIntradayScreenPolicy,
    screen_opening_range_candidate,
)
from financial_brain.features.etf_liquidity import ETFDailyLiquidityObservation
from financial_brain.forward_edge.market_data import normalize_kite_quote


DAY = date(2026, 11, 13)
OPEN = datetime(2026, 11, 13, 3, 45, tzinfo=timezone.utc)
AS_OF = OPEN + timedelta(minutes=35)
CLOSE = datetime(2026, 11, 13, 10, 0, tzinfo=timezone.utc)


def _bars(*, breakout: float = 102.0, available_offset: timedelta = timedelta(minutes=4)):
    rows = []
    for index in range(7):
        start = OPEN + timedelta(minutes=5 * index)
        close = breakout if index == 6 else 100.0
        rows.append({
            "venue": "BSE", "instrument_id": "BSE:ETF1", "session_date": DAY.isoformat(),
            "interval_minutes": 5, "bucket_start": start.isoformat(),
            "available_at": start + available_offset,
            "open": 100.0, "high": max(101.0, close), "low": min(99.0, close),
            "close": close, "source_event_ids": [f"event-{index}"],
            "input_sha256": f"{index + 1:064x}",
        })
    return rows


def _quotes(*, bid: float = 99.99, ask: float = 100.01, quantity: int = 1000):
    event_time = datetime(2026, 11, 13, 4, 18, tzinfo=timezone.utc)
    return [normalize_kite_quote(
        "BSE:ETF1", {
            "timestamp": event_time.isoformat(), "last_price": 100.0, "volume": 1000,
            "depth": {"buy": [{"price": bid, "quantity": quantity}],
                      "sell": [{"price": ask, "quantity": quantity}]},
        }, received_at=event_time + timedelta(seconds=1),
    )]


def _screen(*, bars=None, quotes=None, policy=None, as_of=AS_OF):
    daily = [
        ETFDailyLiquidityObservation(date(2026, 11, 10), 100, 1_000_000, "1" * 64),
        ETFDailyLiquidityObservation(date(2026, 11, 11), 101, 1_100_000, "2" * 64),
        ETFDailyLiquidityObservation(date(2026, 11, 12), 102, 1_200_000, "3" * 64),
    ]
    return screen_opening_range_candidate(
        bars=bars if bars is not None else _bars(), venue="BSE", instrument_id="BSE:ETF1",
        session_date=DAY, session_open=OPEN, session_close=CLOSE, as_of=as_of, daily=daily,
        quotes=quotes if quotes is not None else _quotes(),
        expected_sessions=[row.session_date for row in daily],
        policy=policy or ETFIntradayScreenPolicy(
            intended_notional_inr=100, other_round_trip_cost_bps=1,
            max_quote_age=timedelta(seconds=3), max_session_gap_fraction=0,
            max_stale_quote_fraction=0, max_participation_fraction=0.1,
            max_estimated_all_in_cost_bps=20, min_top_depth_multiple=1,
        ),
    )


def test_opening_range_candidate_requires_cost_liquidity_and_depth_gates():
    result = _screen()
    assert result.status == "ELIGIBLE_CANDIDATE"
    assert result.direction == "LONG"
    assert result.estimated_all_in_round_trip_cost_bps is not None
    assert result.observed_extension_bps >= 2 * result.estimated_all_in_round_trip_cost_bps
    assert result.candidate_only and result.orders_authorized is False
    assert len(result.input_sha256) == 64


def test_wide_spread_thin_depth_or_high_cost_rejects_candidate():
    wide = _screen(quotes=_quotes(bid=99.0, ask=101.0))
    assert wide.direction is None
    assert "LIQUIDITY_OR_COST_LIMIT_NOT_MET" in wide.reasons
    thin = _screen(quotes=_quotes(quantity=1))
    assert thin.direction is None
    assert "TWO_SIDED_TOP_DEPTH_INSUFFICIENT" in thin.reasons
    costly_policy = ETFIntradayScreenPolicy(
        intended_notional_inr=100, other_round_trip_cost_bps=1,
        max_quote_age=timedelta(seconds=3), max_session_gap_fraction=0,
        max_stale_quote_fraction=0, max_participation_fraction=0.1,
        max_estimated_all_in_cost_bps=0.5,
    )
    costly = _screen(policy=costly_policy)
    assert costly.direction is None


def test_missing_or_duplicate_bars_and_incomplete_latest_bar_fail_closed():
    missing = _bars()
    del missing[3]
    with pytest.raises(ValueError, match="missing or duplicated"):
        _screen(bars=missing)
    duplicate = _bars()
    duplicate[3] = duplicate[2]
    with pytest.raises(ValueError, match="missing or duplicated"):
        _screen(bars=duplicate)
    incomplete = _bars(available_offset=timedelta(minutes=1))
    with pytest.raises(ValueError, match="not complete"):
        _screen(bars=incomplete, as_of=OPEN + timedelta(minutes=34))


def test_missing_lineage_or_naive_timestamp_is_rejected():
    bad_hash = _bars()
    bad_hash[-1]["input_sha256"] = "not-a-source-hash"
    with pytest.raises(ValueError, match="source event IDs"):
        _screen(bars=bad_hash)
    naive = _bars()
    naive[-1]["available_at"] = "2026-11-13T04:16:00"
    with pytest.raises(ValueError, match="timezone aware"):
        _screen(bars=naive)
