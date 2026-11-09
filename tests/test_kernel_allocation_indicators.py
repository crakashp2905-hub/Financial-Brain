from __future__ import annotations

import pytest

from financial_brain.kernel.allocation import (
    AllocationError,
    check_constraints,
    drawdown_scale,
    equal_weight,
    inverse_volatility,
    rebalance_trades,
)
from financial_brain.kernel.indicators import atr, donchian_prior, ema, rsi, session_vwap, sma

CLOSE = [10.0, 11.0, 12.0, 11.0, 13.0, 14.0, 13.0, 15.0]
HIGH = [c + 0.5 for c in CLOSE]
LOW = [c - 0.5 for c in CLOSE]


def test_sma_warmup_is_none_not_invented() -> None:
    out = sma(CLOSE, 3)
    assert out[:2] == [None, None]
    assert out[2] == pytest.approx((10 + 11 + 12) / 3)


def test_indicators_are_causal_truncation_invariant() -> None:
    full = sma(CLOSE, 3)
    for cut in range(3, len(CLOSE)):
        assert sma(CLOSE[:cut], 3) == full[:cut]
        assert ema(CLOSE[:cut], 3) == ema(CLOSE, 3)[:cut]
        assert rsi(CLOSE[:cut], 3) == rsi(CLOSE, 3)[:cut]


def test_ema_seeded_with_sma_then_recursive() -> None:
    out = ema(CLOSE, 3)
    assert out[2] == pytest.approx(11.0)
    alpha = 0.5
    assert out[3] == pytest.approx(alpha * 11.0 + (1 - alpha) * 11.0)


def test_atr_wilder_seed_and_donchian_excludes_current_bar() -> None:
    a = atr(HIGH, LOW, CLOSE, 3)
    assert a[2] is not None and a[1] is None
    d = donchian_prior(HIGH, LOW, 3)
    assert d[2] is None
    hi, lo = d[3]
    assert hi == max(HIGH[0:3]) and lo == min(LOW[0:3])


def test_rsi_extremes_are_defined() -> None:
    up = list(range(1, 12))
    assert rsi([float(x) for x in up], 3)[-1] == pytest.approx(100.0)
    flat = [5.0] * 8
    assert rsi(flat, 3)[-1] == pytest.approx(50.0)


def test_vwap_resets_each_session_and_none_on_zero_volume() -> None:
    prices = [10.0, 12.0, 20.0, 22.0]
    vols = [1.0, 1.0, 0.0, 2.0]
    out = session_vwap(prices, vols, ["d1", "d1", "d2", "d2"])
    assert out[1] == pytest.approx(11.0)
    assert out[2] is None  # zero-volume bar on a new session
    assert out[3] == pytest.approx(22.0)


def test_equal_and_inverse_vol_weights_sum_to_invested_fraction() -> None:
    ew = equal_weight(["A", "B", "C"], invested=0.9)
    assert sum(ew.values()) == pytest.approx(0.9)
    iv = inverse_volatility({"A": 0.1, "B": 0.2}, invested=1.0)
    assert iv["A"] == pytest.approx(2 / 3) and iv["B"] == pytest.approx(1 / 3)


def test_constraints_reject_shorts_and_overallocation() -> None:
    assert check_constraints({"A": -0.1}) == ["SHORT_NOT_AUTHORISED:A"]
    assert "SUM_EXCEEDS_100_PERCENT" in check_constraints({"A": 0.7, "B": 0.6})
    assert check_constraints({"A": 0.5, "B": 0.5}, max_weight=0.6) == []


def test_no_trade_band_prevents_churn() -> None:
    assert rebalance_trades({"A": 0.50}, {"A": 0.51}, band=0.02) == {}
    assert rebalance_trades({"A": 0.50}, {"A": 0.60}, band=0.02) == {"A": pytest.approx(0.10)}


def test_drawdown_scale_only_reduces_exposure() -> None:
    assert drawdown_scale(100, 100, threshold=0.1) == 1.0
    scaled = drawdown_scale(85, 100, threshold=0.1)
    assert 0.5 <= scaled < 1.0
    assert drawdown_scale(10, 100, threshold=0.1) == 0.5  # floor


def test_bad_inputs_rejected() -> None:
    with pytest.raises(AllocationError):
        inverse_volatility({"A": 0.0})
    with pytest.raises(AllocationError):
        equal_weight([])
