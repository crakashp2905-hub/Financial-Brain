"""Sharpe, PSR, deflated Sharpe and Sharpe allocation."""
import numpy as np
import pandas as pd
import pytest

from financial_brain.evaluation import performance as PF


def test_sharpe_of_a_known_series():
    rng = np.random.default_rng(0)
    r = rng.normal(0.01 + PF.RISK_FREE / 12, 0.03, 240)                    # 1% a month above cash, 3% sd
    s = PF.stats(r)
    assert s["sharpe"] == pytest.approx(0.01 / 0.03 * np.sqrt(12), abs=0.25)
    assert 0 < s["max_dd"] < 1 and s["psr"] > 0.95


def test_deflation_punishes_many_trials():
    rng = np.random.default_rng(1)
    r = rng.normal(0.004 + PF.RISK_FREE / 12, 0.04, 120)
    one, many = PF.stats(r), PF.stats(r, trials=300)
    assert many["dsr"] < one["psr"] and many["sharpe_luck"] > 0


def test_the_best_of_many_noise_strategies_is_not_called_skill():
    rng = np.random.default_rng(2)
    sims = [PF.stats(rng.normal(PF.RISK_FREE / 12, 0.04, 120)) for _ in range(300)]
    best = max(sims, key=lambda s: s["sharpe"])
    x = rng.normal(PF.RISK_FREE / 12, 0.04, 120)
    assert best["sharpe"] > 0.5                                          # luck looks like skill...
    assert PF.stats(x, trials=300)["dsr"] < 0.95                         # ...and deflation says so


def test_sharpe_allocation_uses_only_the_past_and_favours_the_steady_strategy():
    rng = np.random.default_rng(3)
    idx = pd.period_range("2010-01", periods=120, freq="M")
    good = rng.normal(0.012, 0.01, 120)
    bad = rng.normal(0.0, 0.05, 120)
    panel = pd.DataFrame({"good": good, "bad": bad}, index=idx)
    out = PF.sharpe_allocation(panel)
    assert out["w_good"].iloc[-1] > out["w_bad"].iloc[-1]
    assert out.index[0] > idx[11]                                        # needed 12 months of history first
