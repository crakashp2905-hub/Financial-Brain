from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.features.streaming_bars import (
    BarWindowStatus,
    MinuteBar,
    StreamingBarAggregator,
    StreamingBarError,
)

UTC = timezone.utc
OPEN = datetime(2026, 11, 23, 3, 45, tzinfo=UTC)  # 09:15 IST
CLOSE = datetime(2026, 11, 23, 10, 0, tzinfo=UTC)  # 15:30 IST


def _minute(offset: int, *, price: float | None = None, available_delay: int = 1) -> MinuteBar:
    px = 100.0 + offset if price is None else price
    return MinuteBar("NSE:INFY", OPEN + timedelta(minutes=offset), px, px + 2, px - 1, px + 1,
                     float(offset + 1), OPEN + timedelta(minutes=offset + available_delay))


def _aggregate() -> StreamingBarAggregator:
    return StreamingBarAggregator(instrument_id="NSE:INFY", session_open=OPEN, session_close=CLOSE)


def test_all_requested_frequencies_aggregate_only_complete_full_session_bars() -> None:
    agg = _aggregate()
    for offset in reversed(range(375)):
        assert agg.ingest(_minute(offset))
    rows = agg.snapshot(as_of=CLOSE)
    counts = {frequency: sum(row.frequency == frequency for row in rows)
              for frequency in ("1m", "5m", "15m", "30m", "1h", "1D")}
    assert counts == {"1m": 375, "5m": 75, "15m": 25, "30m": 12, "1h": 6, "1D": 1}
    daily = next(row for row in rows if row.frequency == "1D")
    assert daily.is_complete
    assert daily.expected_minutes == daily.observed_minutes == 375
    assert daily.open == 100.0 and daily.close == 475.0
    assert daily.high == 476.0 and daily.low == 99.0
    assert daily.volume == sum(float(offset + 1) for offset in range(375))


def test_aggregated_bar_is_invisible_until_end_and_minute_availability() -> None:
    agg = _aggregate()
    for offset in range(5):
        agg.ingest(_minute(offset))
    before_end = agg.snapshot(as_of=OPEN + timedelta(minutes=4), frequencies=("5m",))
    assert before_end == ()
    at_end = agg.snapshot(as_of=OPEN + timedelta(minutes=5), frequencies=("5m",))
    assert len(at_end) == 1 and at_end[0].is_complete
    assert at_end[0].close == 105.0


def test_missing_minutes_are_flagged_without_synthetic_ohlcv() -> None:
    agg = _aggregate()
    for offset in (0, 1, 3, 4):
        agg.ingest(_minute(offset))
    windows = agg.snapshot(as_of=OPEN + timedelta(minutes=5), frequencies=("5m",))
    assert len(windows) == 1
    assert windows[0].status is BarWindowStatus.MISSING_MINUTES
    assert windows[0].expected_minutes == 5 and windows[0].observed_minutes == 4
    assert windows[0].missing_minute_starts == (OPEN + timedelta(minutes=2),)
    assert all(getattr(windows[0], name) is None for name in ("open", "high", "low", "close", "volume"))
    assert agg.snapshot(as_of=OPEN + timedelta(minutes=5), frequencies=("5m",)) == windows


def test_future_available_minute_is_not_used_at_an_earlier_cutoff() -> None:
    agg = _aggregate()
    for offset in range(5):
        delay = 3 if offset == 4 else 1
        agg.ingest(_minute(offset, available_delay=delay))
    before_availability = agg.snapshot(as_of=OPEN + timedelta(minutes=5), frequencies=("5m",))
    assert before_availability[0].observed_minutes == 4
    assert before_availability[0].missing_minute_starts == (OPEN + timedelta(minutes=4),)
    after_availability = agg.snapshot(as_of=OPEN + timedelta(minutes=7), frequencies=("5m",))
    assert after_availability[0].is_complete


def test_exact_duplicates_are_idempotent_but_conflicting_revisions_fail_closed() -> None:
    agg = _aggregate()
    row = _minute(0)
    assert agg.ingest(row)
    assert not agg.ingest(row)
    with pytest.raises(StreamingBarError, match="conflicting minute revision"):
        agg.ingest(_minute(0, price=999.0))


def test_malformed_or_out_of_session_minutes_are_rejected() -> None:
    with pytest.raises(StreamingBarError, match="timezone-aware"):
        StreamingBarAggregator(instrument_id="NSE:INFY", session_open=datetime(2026, 11, 23, 3, 45),
                               session_close=CLOSE)
    agg = _aggregate()
    with pytest.raises(StreamingBarError, match="outside"):
        agg.ingest(_minute(375))
    with pytest.raises(StreamingBarError, match="before its interval"):
        MinuteBar("NSE:INFY", OPEN, 10, 11, 9, 10, 100, OPEN + timedelta(seconds=30))
    with pytest.raises(StreamingBarError, match="inconsistent"):
        MinuteBar("NSE:INFY", OPEN, 10, 9, 8, 10, 100, OPEN + timedelta(minutes=1))
