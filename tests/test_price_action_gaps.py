from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.strategies.price_action_gaps import (
    Candle, advance_gap, detect_fair_value_gaps,
)


T0 = datetime(2026, 11, 2, 9, 15, tzinfo=timezone.utc)


def candle(index, low, high, close=None):
    return Candle(f"c{index}", T0 + timedelta(minutes=index), high, low,
                  close if close is not None else (low + high) / 2)


def test_bullish_gap_is_only_available_at_third_candle_close():
    candles = [candle(0, 99, 100), candle(1, 103, 105), candle(2, 102, 104)]
    gap, = detect_fair_value_gaps(candles)
    assert (gap.side, gap.lower, gap.upper) == ("BULLISH", 100, 102)
    assert gap.available_at == candles[2].closed_at
    with pytest.raises(ValueError, match="after availability"):
        advance_gap(gap, candles[2])


def test_bearish_gap_and_inversion_are_point_in_time_events():
    candles = [candle(0, 104, 106), candle(1, 101, 103), candle(2, 102, 103)]
    gap, = detect_fair_value_gaps(candles)
    assert (gap.side, gap.lower, gap.upper) == ("BEARISH", 103, 104)
    inverted = advance_gap(gap, candle(3, 104, 107, close=105))
    assert inverted.state == "INVERTED"
    assert inverted.inverted_side == "BULLISH"
    assert inverted.state_changed_by == "c3"
    assert gap.state == "ACTIVE"  # Earlier snapshot does not repaint.


def test_overlap_mitigates_gap_but_touching_only_boundary_does_not_form_one():
    candles = [candle(0, 99, 100), candle(1, 101, 103), candle(2, 100, 102)]
    assert detect_fair_value_gaps(candles) == []
    gap, = detect_fair_value_gaps([candle(0, 99, 100), candle(1, 103, 105), candle(2, 102, 104)])
    mitigated = advance_gap(gap, candle(3, 101, 103))
    assert mitigated.state == "MITIGATED"
    assert mitigated.state_changed_at == T0 + timedelta(minutes=3)


def test_rejects_unordered_or_naive_close_timestamps():
    with pytest.raises(ValueError, match="strictly ordered"):
        detect_fair_value_gaps([candle(1, 99, 100), candle(0, 101, 103), candle(2, 102, 104)])
    with pytest.raises(ValueError, match="timezone-aware"):
        detect_fair_value_gaps([Candle("naive", datetime(2026, 11, 2), 2, 1, 1.5)])
