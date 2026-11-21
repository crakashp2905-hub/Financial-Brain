from __future__ import annotations

from decimal import Decimal

import pytest

from financial_brain.kernel.closures_l import (
    calibration_curve,
    futures_mean_reversion,
    trend_baselines,
    venue_tick,
)


def test_mean_reversion_goes_long_on_a_stretched_low_from_the_next_bar() -> None:
    values: list[float | None] = [10.0, 11.0, 9.0, 10.0, 2.0, 10.0]  # prior window at bar 4 has variation
    signal = futures_mean_reversion(values, window=4, entry_z=1.0)
    assert signal[4] == 0
    assert signal[5] == 1
    with pytest.raises(ValueError):
        futures_mean_reversion(values, window=4, entry_z=0.0)


def test_venue_tick_follows_the_price_tier() -> None:
    assert venue_tick("NSE", Decimal("50")) == Decimal("0.05")
    assert venue_tick("NSE", Decimal("150")) == Decimal("0.10")
    assert venue_tick("CME", Decimal("5000")) == Decimal("0.25")
    with pytest.raises(KeyError):
        venue_tick("UNKNOWN", Decimal("1"))


def test_trend_baselines_return_both_signals_with_no_same_bar_entry() -> None:
    close = [float(i) for i in range(1, 9)]
    out = trend_baselines(close, close, close, n=3, fast=2, slow=4)
    assert set(out) == {"donchian", "ma_crossover"}
    assert out["donchian"][0] == 0 and out["ma_crossover"][0] == 0
    assert len(out["donchian"]) == len(close)


def test_calibration_curve_matches_bin_means() -> None:
    curve = calibration_curve([0.1, 0.15, 0.9], [0, 0, 1], bins=2)
    assert curve[0][2] == 2
    assert curve[0][1] == pytest.approx(0.0)
    assert curve[-1][1] == pytest.approx(1.0)
