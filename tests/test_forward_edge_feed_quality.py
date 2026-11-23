from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.forward_edge.feed_quality import FeedQualityError, audit_feed
from financial_brain.forward_edge.market_events import EventKind, MarketEvent

UTC = timezone.utc
OPEN = datetime(2026, 11, 23, 9, 15, tzinfo=UTC)
CLOSE = OPEN + timedelta(minutes=5)


def _bar(event_id: str, stamp: datetime, *, complete: bool | None = None) -> MarketEvent:
    payload = {"open": 100, "high": 102, "low": 99, "close": 101, "volume": 10}
    if complete is not None:
        payload["is_complete"] = complete
    return MarketEvent(event_id, EventKind.BAR, "NSE", "NSE:INFY", stamp, stamp,
                       stamp, payload, "fixture")


def _quote(event_id: str, stamp: datetime, *, sequence: int) -> MarketEvent:
    return MarketEvent(event_id, EventKind.QUOTE, "NSE", "NSE:INFY", stamp, stamp,
                       stamp, {"bid": 100, "ask": 101}, "fixture", sequence=sequence)


def test_audit_feed_detects_all_required_quality_failures() -> None:
    crossed = _quote("crossed", OPEN + timedelta(seconds=10), sequence=2)
    crossed.payload["bid"] = 102  # simulate post-parse mutation at a hostile adapter boundary
    events = [
        crossed,
        MarketEvent("tick-old", EventKind.TICK, "NSE", "NSE:INFY",
                    OPEN + timedelta(seconds=5), OPEN + timedelta(seconds=5),
                    OPEN + timedelta(seconds=5), {"price": 100}, "fixture", sequence=1),
        _bar("misanchored", OPEN + timedelta(minutes=1, seconds=30), complete=False),
    ]
    report = audit_feed(
        events, venue="NSE", instrument_id="NSE:INFY", session_open=OPEN,
        session_close=CLOSE, as_of=OPEN + timedelta(minutes=3, seconds=30),
        max_tick_age=timedelta(seconds=1),
    )
    codes = {finding.code for finding in report.findings}
    assert not report.ready
    assert {
        "PARTIAL_CANDLE", "MISSING_MINUTE", "WRONG_INTERVAL_ANCHOR",
        "SESSION_OPEN_GAP", "STALE_TICK", "NONMONOTONIC_TIMESTAMP",
        "CROSSED_MARKET", "OUT_OF_SEQUENCE",
    } <= codes


def test_audit_feed_accepts_complete_anchored_bar_series() -> None:
    events = [_bar("bar-1", OPEN), _bar("bar-2", OPEN + timedelta(minutes=1))]
    report = audit_feed(events, venue="NSE", instrument_id="NSE:INFY",
                        session_open=OPEN, session_close=CLOSE,
                        as_of=OPEN + timedelta(minutes=2))
    assert report.ready
    assert report.findings == ()


def test_audit_feed_flags_an_open_gap_when_the_session_has_no_bars() -> None:
    report = audit_feed([], venue="NSE", instrument_id="NSE:INFY",
                        session_open=OPEN, session_close=CLOSE,
                        as_of=OPEN + timedelta(minutes=1))
    assert {finding.code for finding in report.findings} == {
        "SESSION_OPEN_GAP", "MISSING_MINUTE",
    }


def test_unavailable_bar_cannot_satisfy_session_coverage() -> None:
    stamp = OPEN
    future_available = MarketEvent(
        "future-bar", EventKind.BAR, "NSE", "NSE:INFY", stamp, stamp,
        stamp + timedelta(minutes=2),
        {"open": 100, "high": 102, "low": 99, "close": 101, "volume": 10},
        "fixture",
    )
    report = audit_feed([future_available], venue="NSE", instrument_id="NSE:INFY",
                        session_open=OPEN, session_close=CLOSE,
                        as_of=OPEN + timedelta(minutes=1))
    codes = {finding.code for finding in report.findings}
    assert {"NOT_AVAILABLE_AT_AS_OF", "SESSION_OPEN_GAP", "MISSING_MINUTE"} <= codes


def test_instrument_session_metrics_report_coverage_latency_quote_depth_and_strikes() -> None:
    bad_bar = _bar("bar-bad", OPEN + timedelta(minutes=1))
    bad_bar.payload["high"] = 100  # hostile adapter mutation after event validation
    tick = MarketEvent(
        "late-tick", EventKind.TICK, "NSE", "NSE:INFY", OPEN, OPEN + timedelta(seconds=2),
        OPEN + timedelta(seconds=2), {"price": 100}, "fixture")
    quote = MarketEvent(
        "quote-depth", EventKind.QUOTE, "NSE", "NSE:INFY", OPEN + timedelta(seconds=1),
        OPEN + timedelta(seconds=1), OPEN + timedelta(seconds=1),
        {"bid": 100, "ask": 101, "bid_quantity": 10, "ask_quantity": 12,
         "depth": {"buy": [[100, 10]], "sell": [[101, 12]]},
         "selected_strike": 22000, "target_strike": 21900}, "fixture")
    report = audit_feed(
        [_bar("bar-good", OPEN), bad_bar, tick, quote],
        venue="NSE", instrument_id="NSE:INFY", session_open=OPEN,
        session_close=CLOSE, as_of=OPEN + timedelta(minutes=2),
    )
    metrics = report.metrics
    assert report.session_date.isoformat() == "2026-11-23"
    assert (metrics.expected_bars, metrics.complete_bars) == (2, 1)
    assert metrics.bar_coverage_fraction == pytest.approx(0.5)
    assert (metrics.tick_count, metrics.late_tick_count) == (1, 1)
    assert metrics.late_tick_rate == pytest.approx(1.0)
    assert metrics.quote_age_max_seconds == pytest.approx(119.0)
    assert metrics.depth_validity_fraction == pytest.approx(1.0)
    assert metrics.inconsistent_ohlc_count == 1
    assert metrics.strike_selection_count == 1
    assert metrics.strike_selection_mae == pytest.approx(100 / 21900)
    assert "INCONSISTENT_OHLC" in {finding.code for finding in report.findings}


@pytest.mark.parametrize("field,value", [
    ("session_open", datetime(2026, 11, 23, 9, 15)),
    ("session_close", datetime(2026, 11, 23, 9, 20)),
    ("as_of", datetime(2026, 11, 23, 9, 17)),
])
def test_audit_feed_requires_timezone_aware_session_clocks(field: str, value: datetime) -> None:
    arguments = {"session_open": OPEN, "session_close": CLOSE, "as_of": OPEN}
    arguments[field] = value
    with pytest.raises(FeedQualityError, match="timezone aware"):
        audit_feed([], venue="NSE", instrument_id="NSE:INFY", **arguments)
