from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.strategies.opening_range_breakout import (
    MinuteBar, evaluate_opening_range,
)


OPEN = datetime(2026, 11, 4, 9, 15, tzinfo=timezone(timedelta(hours=5, minutes=30)))


def bar(minute, low, high, close, volume=100):
    return MinuteBar(f"m{minute}", OPEN + timedelta(minutes=minute), high, low, close, volume)


def test_five_minute_breakout_is_unavailable_until_complete_then_signals_on_close():
    bars = [bar(1, 99, 100, 99.5), bar(2, 99, 101, 100), bar(3, 99, 100, 99.5),
            bar(4, 98, 100, 99), bar(5, 99, 100, 99.5)]
    assert evaluate_opening_range(bars[:4], session_open=OPEN, minutes=5,
                                  buffer=0.2, volume_multiplier=1.2) is None
    result = evaluate_opening_range(bars + [bar(6, 100, 102, 101.3, 150)], session_open=OPEN,
                                    minutes=5, buffer=0.2, volume_multiplier=1.2)
    assert result is not None
    assert result.status == "ACTIVE" and result.side == "LONG"
    assert result.range_complete_at == OPEN + timedelta(minutes=5)
    assert result.signal_at == OPEN + timedelta(minutes=6)


def test_volume_gate_close_through_and_invalidation_are_explicit():
    opening = [bar(i, 99, 100, 99.5) for i in range(1, 6)]
    low_volume = bar(6, 100, 102, 101, 99)
    qualified = bar(7, 100, 102, 101, 120)
    invalidation = bar(8, 98, 100, 100)
    result = evaluate_opening_range(opening + [low_volume, qualified, invalidation],
                                    session_open=OPEN, minutes=5, buffer=0.5,
                                    volume_multiplier=1.1)
    assert result is not None and result.status == "INVALIDATED"
    assert result.signal_at == qualified.closed_at
    assert result.invalidated_at == invalidation.closed_at


def test_fifteen_minute_short_breakout_and_incomplete_range():
    opening = [bar(i, 100, 101, 100.5) for i in range(1, 16)]
    result = evaluate_opening_range(opening + [bar(16, 98, 100, 99)], session_open=OPEN,
                                    minutes=15, buffer=0.25, volume_multiplier=1)
    assert result is not None and result.side == "SHORT"
    missing = [item for item in opening if item.candle_id != "m8"]
    assert evaluate_opening_range(missing, session_open=OPEN, minutes=15,
                                  buffer=0, volume_multiplier=0) is None


def test_rejects_unsupported_window_naive_open_and_bad_bars():
    with pytest.raises(ValueError, match="5/15-minute"):
        evaluate_opening_range([], session_open=OPEN, minutes=30,
                                buffer=0, volume_multiplier=1)
    with pytest.raises(ValueError, match="timezone-aware"):
        evaluate_opening_range([], session_open=datetime(2026, 11, 4), minutes=5,
                                buffer=0, volume_multiplier=1)
    with pytest.raises(ValueError, match="inconsistent"):
        evaluate_opening_range([MinuteBar("x", OPEN + timedelta(minutes=1), 2, 3, 2.5, 1)],
                               session_open=OPEN, minutes=5, buffer=0, volume_multiplier=1)
