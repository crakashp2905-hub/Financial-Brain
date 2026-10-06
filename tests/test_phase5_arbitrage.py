"""Spreads and arbitrage: the cointegration test, pairs on a planted relation, PCA residual reversal."""
import numpy as np
import pandas as pd
import pytest

from financial_brain.evaluation import arbitrage as AR


def test_the_adf_t_separates_a_stationary_series_from_a_random_walk():
    rng = np.random.default_rng(0)
    e = np.zeros(500)
    for i in range(1, 500):
        e[i] = 0.7 * e[i - 1] + rng.normal()
    assert AR.adf_t(e) < AR.ADF_5PCT_EG
    assert AR.adf_t(np.cumsum(rng.normal(size=500))) > AR.ADF_5PCT_EG


def market(n=900, names=20, seed=1, pair_strength=0.0):
    rng = np.random.default_rng(seed)
    common = rng.normal(0, 0.01, n)
    r = rng.normal(0, 0.015, (n, names)) + common[:, None]
    if pair_strength:
        # names 0 and 1 share a mean-reverting spread
        s = np.zeros(n)
        for i in range(1, n):
            s[i] = 0.9 * s[i - 1] + rng.normal(0, 0.01)
        r[:, 1] = r[:, 0] - np.diff(np.concatenate([[0], s])) * pair_strength
    px = pd.DataFrame(100 * np.cumprod(1 + r, axis=0), index=pd.bdate_range("2018-01-01", periods=n))
    return px


def test_a_planted_mean_reverting_pair_is_found_and_earns_gross():
    res = AR.pairs_coint(market(pair_strength=1.0), n_pairs=1, cost=0.0)
    assert len(res) and res["gross"].mean() > 0


def test_pca_statarb_runs_and_charges_costs():
    px = market(n=400, names=40)
    out = AR.pca_statarb(px.pct_change().dropna(), window=200, k=5, step=5)
    assert len(out) > 10 and (out["net"] <= out["gross"] + 1e-12).all()
