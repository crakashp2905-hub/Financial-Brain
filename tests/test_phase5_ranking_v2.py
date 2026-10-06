"""Ranking v2: continuous, momentum-led, never ordered by cost, refuses to price without frozen slopes."""
import numpy as np
import pandas as pd
import pytest

from financial_brain.opportunity import ranking_v2 as R2


def cs(n=300, seed=0):
    rng = np.random.default_rng(seed)
    return pd.DataFrame({"ticker": [f"T{i}" for i in range(n)], "price": rng.uniform(50, 2000, n),
                         "adv20": rng.uniform(1e7, 5e9, n), "vol_60": rng.uniform(0.15, 0.7, n),
                         "mom_12_1": rng.normal(0.1, 0.3, n), "dist_52w_high": -rng.uniform(0, 0.5, n)})


def test_the_order_is_the_score_alone_not_cost():
    c = cs()
    r = R2.rank(c, horizon=20, slopes={20: 0.01})
    assert r["score"].is_monotonic_decreasing
    assert r["ticker"].iloc[0] == c.assign(s=R2.score(c)).sort_values("s").iloc[-1]["ticker"]
    assert r["score"].duplicated().mean() < 0.02                    # near-continuous: almost nothing to tie-break


def test_the_slopes_are_frozen_from_the_calibration_period():
    assert R2.SLOPE == {20: 0.007269, 60: 0.020438}


def test_expected_excess_is_the_frozen_slope_times_the_score_and_net_subtracts_itemised_costs():
    r = R2.rank(cs(), horizon=20, slopes={20: 0.01})
    assert np.allclose(r["expected_return"], 0.01 * r["score"])
    a = r["alpha"].iloc[0]
    assert r["expected_net"].iloc[0] == pytest.approx(a["net"]) and a["spread"] > 0


def test_no_slope_no_price():
    with pytest.raises(R2.RankingError, match="calibrate"):
        R2.rank(cs(), horizon=20, slopes={})


def test_the_slope_recovers_a_planted_relation():
    rng = np.random.default_rng(2)
    s = pd.Series(rng.normal(0, 1, 2000))
    fwd = 0.004 * s + rng.normal(0, 0.05, 2000) + 0.01
    assert R2.slope(s, fwd) == pytest.approx(0.004, abs=0.0015)
