from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.strategies.liquidity_sweeps import (
    ConfirmedLevel, detect_liquidity_sweep,
)
from financial_brain.strategies.price_action_gaps import Candle


START = datetime(2026, 11, 3, 9, 15, tzinfo=timezone.utc)


def bar(i, low, high, close):
    return Candle(f"b{i}", START + timedelta(minutes=i), high, low, close)


def test_high_sweep_requires_confirmed_level_threshold_and_later_reclaim():
    level = ConfirmedLevel("prev-high", "HIGH", 100, START)
    result = detect_liquidity_sweep(
        level, [bar(1, 99, 100.5, 100), bar(2, 99, 101.2, 100.8), bar(3, 98, 100, 99.8)],
        threshold=1, max_confirmation_bars=2)
    assert result is not None
    assert (result.sweep_candle_id, result.reclaim_candle_id, result.bars_to_reclaim) == (
        "b2", "b3", 1)


def test_low_sweep_mirrors_high_and_fails_after_confirmation_deadline():
    low_level = ConfirmedLevel("prev-low", "LOW", 50, START)
    found = detect_liquidity_sweep(
        low_level, [bar(1, 48.8, 51, 50), bar(2, 49.5, 52, 50.2)],
        threshold=1, max_confirmation_bars=1)
    assert found is not None and found.side == "LOW"
    expired = detect_liquidity_sweep(
        ConfirmedLevel("prev-high", "HIGH", 100, START),
        [bar(1, 99, 101.1, 101), bar(2, 100, 102, 101), bar(3, 99, 100, 99)],
        threshold=1, max_confirmation_bars=1)
    assert expired is None


def test_same_candle_wick_and_close_is_not_claimed_as_reclaim():
    level = ConfirmedLevel("h", "HIGH", 100, START)
    result = detect_liquidity_sweep(
        level, [bar(1, 99, 102, 99), bar(2, 98, 101, 99)],
        threshold=1, max_confirmation_bars=1)
    assert result is not None  # Reclaim is recorded only at the second bar close.
    assert result.sweep_candle_id == "b1" and result.reclaim_candle_id == "b2"


def test_rejects_invalid_threshold_window_and_unordered_bars():
    level = ConfirmedLevel("h", "HIGH", 100, START)
    with pytest.raises(ValueError, match="positive"):
        detect_liquidity_sweep(level, [], threshold=0, max_confirmation_bars=2)
    with pytest.raises(ValueError, match="strictly ordered"):
        detect_liquidity_sweep(level, [bar(2, 99, 101, 100), bar(1, 99, 101, 100)],
                               threshold=1, max_confirmation_bars=2)
