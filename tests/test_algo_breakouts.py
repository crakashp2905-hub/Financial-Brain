"""Breakout family tests: Donchian, Turtle, ATR threshold, two-break, gap fills, sizing, controls (ALG-044..053, ALG-064, ALG-111)."""

from __future__ import annotations

from financial_brain.strategies.breakouts import (
    TURTLE_ENTRY,
    TURTLE_EXIT,
    atr_n_position_size,
    atr_past_only,
    atr_threshold_trigger,
    basic_breakouts,
    donchian_lower,
    donchian_upper,
    gap_aware_fill,
    incremental_pnl,
    trend_filtered_breakouts,
    turtle_exit_level,
    two_break_confirmation,
)
from financial_brain.strategies.mean_reversion import rolling_zscore


def _series(n: int = 60) -> tuple[list[float], list[float]]:
    closes = [100.0 + 0.1 * i for i in range(n)]
    closes[40] = 110.0  # one clear breakout above the prior 20-bar high
    highs = [c + 0.2 for c in closes]
    return closes, highs


def test_donchian_upper_excludes_the_current_bar() -> None:  # ALG-044
    _c, highs = _series()
    t = 40
    assert donchian_upper(highs, t, 20) == max(highs[t - 20 : t])
    spiked = list(highs)
    spiked[t] = 999.0
    assert donchian_upper(spiked, t, 20) == donchian_upper(highs, t, 20)


def test_basic_breakout_fires_only_on_a_close_above_the_prior_high() -> None:  # ALG-045
    closes, highs = _series()
    sigs = basic_breakouts(closes, highs, 20)
    assert [s.index for s in sigs] == [40]
    assert sigs[0].side == "LONG"


def test_turtle_constants_are_fixed() -> None:  # ALG-046
    assert (TURTLE_ENTRY, TURTLE_EXIT) == (20, 10)
    lows = [99.0] * 30
    assert turtle_exit_level(lows, 20) == 99.0


def test_atr_sizing_fails_closed_on_bad_inputs() -> None:  # ALG-047
    assert atr_n_position_size(capital=1_000_000, risk_fraction=0.01, atr=5.0, price=100.0) > 0
    assert atr_n_position_size(capital=1_000_000, risk_fraction=0.01, atr=0.0, price=100.0) == 0
    assert atr_n_position_size(capital=1_000, risk_fraction=0.01, atr=1.0, price=500.0) == 0  # unaffordable lot
    assert atr_n_position_size(capital=1e6, risk_fraction=0.5, atr=1.0, price=10.0) == 0  # risk cap


def test_two_break_is_a_separate_hypothesis_with_its_own_name() -> None:  # ALG-048
    closes, highs = _series()
    closes[41] = 111.0
    highs = [c + 0.2 for c in closes]
    sigs = two_break_confirmation(closes, highs, 20)
    assert sigs and sigs[0].rule.startswith("two_break")
    assert all(s.index >= 41 for s in sigs)


def test_trend_filter_is_a_subset_of_basic_breakouts() -> None:  # ALG-049
    closes, highs = _series(120)
    basic = {s.index for s in basic_breakouts(closes, highs, 20)}
    filtered = {s.index for s in trend_filtered_breakouts(closes, highs, ema_period=50, n=20)}
    assert filtered <= basic


def test_atr_threshold_trigger_uses_past_atr_only() -> None:  # ALG-050
    highs = [10.0 + 0.5 * (i % 3) for i in range(40)]
    lows = [h - 1.0 for h in highs]
    closes = [h - 0.5 for h in highs]
    atr = atr_past_only(highs, lows, closes, 30, 14)
    assert atr is not None
    big_close = list(closes)
    big_close[30] = 1e6  # changing bar t's own range must not change the ATR used at t
    assert atr_past_only(highs, lows, big_close, 30, 14) == atr
    assert atr_threshold_trigger(highs, lows, closes, 30, k=0.0, prior_high=max(highs[:30])) is False


def test_gap_through_trigger_fills_at_the_open() -> None:  # ALG-052
    assert gap_aware_fill(prev_close=100.0, open_=106.0, trigger=105.0) == 106.0
    assert gap_aware_fill(prev_close=100.0, open_=104.0, trigger=105.0) == 105.0
    assert gap_aware_fill(prev_close=100.0, open_=0.0, trigger=105.0) is None


def test_incremental_pnl_is_a_paired_difference() -> None:  # ALG-053
    out = incremental_pnl([0.02, -0.01, 0.03], [0.01, 0.0, 0.01])
    assert round(out["incremental_total"], 6) == 0.02  # 0.01 - 0.01 + 0.02
    assert out["bars"] == 3.0


def test_zscore_uses_only_bars_before_t() -> None:  # ALG-064
    closes = [100.0 + (i % 5) for i in range(40)]
    z = rolling_zscore(closes, 30, 20)
    spiked = list(closes)
    spiked[30] = 500.0
    assert rolling_zscore(spiked, 30, 20) is not None and z is not None
    assert rolling_zscore(spiked, 30, 20) != z  # bar t itself changes the value, but the window before t does not
    window_only = list(closes)
    window_only[39] = 500.0  # a bar after t must not affect the value at t
    assert rolling_zscore(window_only, 30, 20) == z


def test_breakout_results_are_invariant_to_price_scale_and_shift() -> None:  # ALG-111
    closes, highs = _series()
    base = [s.index for s in basic_breakouts(closes, highs, 20)]
    scaled_c = [c * 7.5 for c in closes]
    scaled_h = [h * 7.5 for h in highs]
    shifted_c = [c + 250.0 for c in closes]
    shifted_h = [h + 250.0 for h in highs]
    assert [s.index for s in basic_breakouts(scaled_c, scaled_h, 20)] == base
    assert [s.index for s in basic_breakouts(shifted_c, shifted_h, 20)] == base


def test_donchian_lower_mirrors_upper() -> None:
    lows = [100.0 - i * 0.1 for i in range(30)]
    assert donchian_lower(lows, 25, 10) == min(lows[15:25])
