from __future__ import annotations

import pytest

from financial_brain.forecasting.index_extras import (
    option_paths,
    persistence_baseline,
    prediction_interval,
    rank_with_tails,
    vol_tod_baseline,
)


def test_baselines_sum_to_one():
    assert sum(persistence_baseline("UP").values()) == 1
    p = vol_tod_baseline({3: [0.001, 0.002, 0.005, 0.0005]}, 3, 0.0015)
    assert p["FLAT"] == 0.5 and sum(p.values()) == pytest.approx(1)
    assert vol_tod_baseline({}, 1, 0.1)["UP"] == pytest.approx(1 / 3)


def test_prediction_interval():
    lo, hi = prediction_interval([i / 100 for i in range(-10, 11)], 0.8)
    assert lo < 0 < hi
    with pytest.raises(ValueError):
        prediction_interval([0.1] * 5)


def test_option_paths_theta_and_iv_crush_make_loss_likely():
    calm = option_paths(24000, 0.0005, 15, delta=0.5, gamma=0.0005, theta_per_step=-0.5, vega=10,
                        iv_shock=-0.5, entry_ask=100, exit_spread=2)
    assert calm["mean"] < 0 and calm["p_loss"] > 0.5 and calm["p05"] < calm["p95"]


def test_rank_with_tails_penalises_fat_left_tail():
    r = rank_with_tails({"A": {"mean": 10, "p05": -100}, "B": {"mean": 8, "p05": -20}}, 0.1, 0)
    assert r[0][0] == "B"
