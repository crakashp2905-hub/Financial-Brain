import numpy as np
import pandas as pd
import pytest

from financial_brain.evaluation import exit_rules as X
from financial_brain.evaluation import market_timing as M


def series(n=600, drift=0.0004, vol=0.01, seed=0):
    r = np.random.default_rng(seed).normal(drift, vol, n)
    return pd.Series(100 * np.cumprod(1 + r), index=pd.bdate_range("2018-01-01", periods=n))


# ----------------------------------------------------------------- timing
def test_the_position_on_day_t_never_uses_day_t_information():
    c = series()
    flip = pd.Series(True, index=c.index)
    flip.iloc[300:] = False
    ret = M.run(c, flip, one_way_cost=0.0)
    # state turns False at 300 (decided at the close of 300): day 300's return is still earned, 301's is cash
    assert ret.iloc[300] == pytest.approx(c.pct_change().iloc[300])
    assert ret.iloc[301] == pytest.approx((1.06) ** (1 / 252) - 1)


def test_always_in_equals_buy_and_hold_and_always_out_earns_cash():
    c = series()
    allin = M.run(c, pd.Series(True, index=c.index), one_way_cost=0.0).dropna()
    assert (allin - c.pct_change().loc[allin.index]).abs().max() < 1e-12
    out = M.run(c, pd.Series(False, index=c.index), one_way_cost=0.0).dropna()
    assert out.std() < 1e-12


def test_switching_costs_are_charged_per_change():
    c = series()
    s = pd.Series(([True] * 50 + [False] * 50) * 6, index=c.index[:600])
    free = M.run(c, s, one_way_cost=0.0).dropna()
    paid = M.run(c, s, one_way_cost=0.001).dropna()
    switches = s.shift(1).diff().abs().sum()
    assert (free - paid).sum() == pytest.approx(0.001 * switches, rel=0.05)


def test_rules_are_nan_until_they_have_the_history():
    c = series()
    assert M.trend(c).iloc[:199].isna().all() and M.trend(c).iloc[199:].notna().all()
    assert M.cross(c).iloc[:199].isna().all()
    assert M.volatility(c).iloc[:260].isna().any()


def test_a_trend_rule_cuts_the_drawdown_of_a_crash_and_stats_are_sane():
    r = np.r_[np.full(400, 0.0007), np.full(60, -0.012), np.full(140, 0.0003)]
    c = pd.Series(100 * np.cumprod(1 + r), index=pd.bdate_range("2018-01-01", periods=600))
    t = M.run(c, M.trend(c))
    assert M.stats(t)["max_dd"] > M.stats(c.pct_change())["max_dd"]            # shallower (less negative)


def test_bootstrap_of_identical_series_is_centred_on_zero_and_short_series_refused():
    c = series(900)
    r = c.pct_change()
    b = M.block_bootstrap_sharpe_diff(r, r, reps=200)
    assert abs(b["mean"]) < 1e-9
    with pytest.raises(M.TimingError):
        M.stats(r.iloc[:100])


# ----------------------------------------------------------------- exits
def test_a_fixed_stop_exits_at_the_breach_close_and_earns_cash_after():
    p = np.array([[100.0], [95.0], [91.0], [105.0], [110.0]])               # 8% stop breached on day 2 (91)
    out = X.simulate(p, ("stop", 0.08))
    assert out["fired"][0] and out["hold"][0] == pytest.approx(0.10)
    expected = 0.91 * (1.06 ** (1 / 252)) ** 2 - 1          # sold at 91, then two sessions of cash
    assert out["ret"][0] == pytest.approx(expected, abs=1e-12)


def test_a_stop_that_never_fires_equals_holding():
    p = np.array([[100.0], [101.0], [102.0], [103.0], [104.0]])
    out = X.simulate(p, ("stop", 0.08))
    assert not out["fired"][0] and out["ret"][0] == pytest.approx(0.04)


def test_trailing_stop_locks_in_gains_a_fixed_stop_would_not():
    p = np.array([[100.0], [120.0], [130.0], [115.0], [118.0]])             # peak 130; 115 is 11.5% below
    fixed = X.simulate(p, ("stop", 0.12))
    trail = X.simulate(p, ("trail", 0.10))
    assert not fixed["fired"][0] and trail["fired"][0]
    assert trail["ret"][0] > 0.14                                           # exited near 15%, not at the end


def test_stops_hurt_a_mean_reverting_book_and_help_a_trending_one():
    rng = np.random.default_rng(1)
    n, h = 4000, 20
    mr, tr = [], []
    for kind in ("mr", "tr"):
        out = []
        for _ in range(n):
            r = rng.normal(0, 0.02, h)
            if kind == "mr":                                                # a dip is followed by a rebound
                r[h // 2:] -= 0.3 * r[:h // 2].mean()
            else:                                                           # a move continues
                r[h // 2:] += 0.3 * r[:h // 2].mean()
            out.append(np.r_[100.0, 100 * np.cumprod(1 + r)])
        paths = np.array(out).T
        s = X.simulate(paths, ("stop", 0.06))
        (mr if kind == "mr" else tr).append(s["ret"].mean() - s["hold"].mean())
    assert mr[0] < tr[0]


def test_bad_inputs_are_refused():
    with pytest.raises(X.ExitError):
        X.simulate(np.array([100.0, 99.0]), ("stop", 0.1))
    with pytest.raises(X.ExitError):
        X.simulate(np.ones((5, 2)), ("fancy", 0.1))
