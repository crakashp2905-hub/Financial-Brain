from __future__ import annotations


import pytest

from financial_brain.kernel.options import (
    OptionError,
    bsm_price,
    greeks,
    implied_vol,
    payoff_at_expiry,
    put_call_parity_gap,
)


def test_bsm_matches_textbook_value() -> None:
    # Hull example: S=42, K=40, r=10%, sigma=20%, T=0.5 -> call 4.76, put 0.81
    assert bsm_price(42, 40, 0.5, 0.10, 0.20, call=True) == pytest.approx(4.759, abs=1e-3)
    assert bsm_price(42, 40, 0.5, 0.10, 0.20, call=False) == pytest.approx(0.8086, abs=1e-3)


def test_put_call_parity_holds_for_model_prices() -> None:
    c = bsm_price(100, 105, 0.75, 0.05, 0.25, call=True, div=0.02)
    p = bsm_price(100, 105, 0.75, 0.05, 0.25, call=False, div=0.02)
    assert put_call_parity_gap(c, p, 100, 105, 0.75, 0.05, 0.02) == pytest.approx(0.0, abs=1e-9)


def test_delta_matches_finite_difference() -> None:
    h = 1e-4
    g = greeks(100, 100, 1.0, 0.05, 0.3, call=True)
    fd = (bsm_price(100 + h, 100, 1.0, 0.05, 0.3, True) - bsm_price(100 - h, 100, 1.0, 0.05, 0.3, True)) / (2 * h)
    assert g["delta"] == pytest.approx(fd, abs=1e-5)


def test_vega_matches_finite_difference() -> None:
    h = 1e-5
    g = greeks(100, 100, 1.0, 0.05, 0.3, call=False)
    fd = (bsm_price(100, 100, 1.0, 0.05, 0.3 + h, False) - bsm_price(100, 100, 1.0, 0.05, 0.3 - h, False)) / (2 * h)
    assert g["vega"] == pytest.approx(fd, abs=1e-4)


def test_implied_vol_round_trips() -> None:
    price = bsm_price(100, 110, 0.5, 0.04, 0.35, call=True)
    assert implied_vol(price, 100, 110, 0.5, 0.04, call=True) == pytest.approx(0.35, abs=1e-5)


def test_no_arbitrage_violation_refused_not_guessed() -> None:
    with pytest.raises(OptionError, match="no-arbitrage"):
        implied_vol(999.0, 100, 100, 0.5, 0.0, call=True)


def test_expired_option_has_intrinsic_only() -> None:
    assert bsm_price(120, 100, 0.0, 0.05, 0.3, call=True) == pytest.approx(20.0)
    assert bsm_price(120, 100, 0.0, 0.05, 0.3, call=False) == pytest.approx(0.0)


def test_greeks_need_positive_time() -> None:
    with pytest.raises(OptionError):
        greeks(100, 100, 0.0, 0.05, 0.3, call=True)


def test_debit_vertical_payoff_has_known_max_gain_and_loss() -> None:
    # long 100 call, short 110 call, net debit 3: max loss 3, max gain 7
    legs = [
        {"call": True, "strike": 100.0, "qty": 1, "premium": 5.0},
        {"call": True, "strike": 110.0, "qty": -1, "premium": 2.0},
    ]
    grid = [80.0, 100.0, 103.0, 110.0, 150.0]
    pay = payoff_at_expiry(legs, grid)
    assert min(pay) == pytest.approx(-3.0)
    assert max(pay) == pytest.approx(7.0)
    assert pay[2] == pytest.approx(0.0)  # breakeven at 103
