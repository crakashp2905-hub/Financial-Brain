"""Synthetic options: Black-Scholes sanity, and what selling and hedging volatility must earn on a known path."""
import numpy as np
import pytest

from financial_brain.evaluation import options_synth as OS


def test_put_call_parity_and_delta():
    S, K, T, s = 100.0, 100.0, 0.25, 0.2
    c, p = OS.bs(S, K, T, s, True), OS.bs(S, K, T, s, False)
    assert c - p == pytest.approx(S - K * np.exp(-OS.R * T), abs=1e-9)
    assert 0.5 < OS.delta(S, K, T, s, True) < 0.6


def test_selling_volatility_wins_when_realised_is_below_implied():
    rng = np.random.default_rng(0)
    spot = 100 * np.cumprod(1 + rng.normal(0, 0.005, 2000))          # ~8% realised a year
    vix = np.full(2000, 20.0)                                         # 20% implied
    s = OS.cycles(spot, vix, strategy="short_straddle")
    h = OS.cycles(spot, vix, strategy="hedged_long_straddle")
    assert s.mean() > 0 and h.mean() < 0


def test_the_condor_caps_the_loss_of_the_strangle():
    spot = np.concatenate([np.full(21, 100.0), np.full(30, 70.0)])   # a 30% crash inside one cycle
    vix = np.full(51, 15.0)
    assert OS.cycles(spot, vix, strategy="iron_condor").min() > OS.cycles(spot, vix, strategy="short_strangle").min()
