"""The trader's half-life table: a signal that predicts h-session returns shows excess, hit rate and cost drag."""
import numpy as np
import pandas as pd

from financial_brain.evaluation import half_life_table as H


def market(n_days=700, n=300, seed=0, edge=0.002):
    rng = np.random.default_rng(seed)
    sig = pd.DataFrame(rng.normal(0, 1, (n_days, n)))
    sig = sig.rolling(20, min_periods=1).mean()                   # a slow signal: low turnover
    r = rng.normal(0, 0.02, (n_days, n)) + edge * sig.shift(1).fillna(0).to_numpy()
    close = pd.DataFrame(100 * np.cumprod(1 + r, axis=0))
    return close, {"s": sig}, pd.DataFrame(True, index=close.index, columns=close.columns)


def test_a_real_signal_shows_positive_excess_and_hit_rate_and_pays_for_turnover():
    close, sig, uni = market()
    t = H.table(close, sig, uni, horizons=(5, 20))
    r = t.set_index("h")
    assert (r["excess"] > 0).all() and (r["hit_rate"] > 0.6).all() and (r["ic"] > 0).all()
    assert (r["net"] < r["excess"]).all() and r.loc[20, "mfe"] > 0 > r.loc[20, "mae"]
    assert r.loc[20, "turnover"] > r.loc[5, "turnover"]           # a slow signal changes more between wider rebalances


def test_noise_has_no_edge_and_loses_its_costs():
    close, sig, uni = market(edge=0.0, seed=3)
    r = H.table(close, sig, uni, horizons=(10,)).iloc[0]
    assert abs(r["ic"]) < 0.03 and r["net"] < 0.002


def test_best_horizon_picks_net_per_session():
    t = pd.DataFrame({"signal": ["a", "a"], "h": [5, 60], "net": [0.002, 0.012], "sharpe": [1, 1]})
    assert H.best_horizon(t)["h"].iloc[0] == 5
