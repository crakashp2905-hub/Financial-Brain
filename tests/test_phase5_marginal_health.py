import numpy as np
import pandas as pd
import pytest

from financial_brain.portfolio import marginal as MG
from financial_brain.risk import health as H


def cov():
    names = ["A", "B", "C", "D", "H"]
    vol = np.array([0.10, 0.06, 0.15, 0.05, 0.10])
    rho = np.full((5, 5), 0.1)
    np.fill_diagonal(rho, 1.0)
    rho[2, 4] = rho[4, 2] = 0.95             # C is the same bet as the held name H
    return pd.DataFrame(np.outer(vol, vol) * rho, index=names, columns=names)


def test_an_uncorrelated_lower_alpha_idea_can_beat_a_correlated_higher_alpha_one():
    cands = pd.DataFrame({"ticker": ["A", "B", "C", "D"], "alpha": [0.042, 0.037, 0.034, 0.028]})
    s = MG.scores(cands, cov(), {"H": 0.5}).set_index("ticker")
    assert s.loc["C", "corr_to_book"] > 0.9
    assert s.loc["D", "score"] > s.loc["C", "score"]           # the review's example: D despite lower alpha
    assert s.index[0] in ("B", "D")


def test_with_an_empty_book_the_score_is_an_information_ratio():
    cands = pd.DataFrame({"ticker": ["A", "B"], "alpha": [0.04, 0.03]})
    s = MG.scores(cands, cov(), {}).set_index("ticker")
    assert s.loc["A", "score"] == pytest.approx(0.04 / 0.10, rel=1e-6)
    assert np.isnan(s.loc["A", "corr_to_book"])


def test_missing_covariance_is_refused():
    with pytest.raises(MG.MarginalError):
        MG.scores(pd.DataFrame({"ticker": ["Z"], "alpha": [0.1]}), cov())


def test_new_strategies_start_on_paper_and_say_why():
    h = H.assess(trades=10, mean=0.01, se=0.002)
    assert h["state"] == H.PAPER_ONLY and any("needed before capital" in r for r in h["reasons"])


def test_losing_with_confidence_disables_and_drawdowns_reduce_or_disable():
    assert H.assess(trades=500, mean=-0.002, se=0.0005)["state"] == H.DISABLED
    assert H.assess(trades=500, mean=0.003, se=0.0005, drawdown=0.12)["state"] == H.REDUCE_CAPITAL
    assert H.assess(trades=500, mean=0.003, se=0.0005, drawdown=0.25)["state"] == H.DISABLED


def test_healthy_needs_a_confident_positive_record_and_clean_symptoms():
    assert H.assess(trades=500, mean=0.003, se=0.0005)["state"] == H.HEALTHY
    assert H.assess(trades=500, mean=0.0005, se=0.0005)["state"] == H.WARNING
    assert H.assess(trades=500, mean=0.003, se=0.0005, exec_cost=0.004, modelled_cost=0.001)["state"] == H.REDUCE_CAPITAL
    assert H.assess(trades=500, mean=0.003, se=0.0005, signal_state="WATCH")["state"] == H.WARNING
    w = H.assess(trades=500, mean=0.002, se=0.0002, expected=0.006)
    assert w["state"] == H.WARNING and "under-delivering" in w["reasons"][0]


def test_bad_data_forces_paper_even_for_a_good_record():
    assert H.assess(trades=500, mean=0.003, se=0.0005, data_ok=False)["state"] == H.PAPER_ONLY


def test_one_day_of_trades_is_never_enough_for_a_verdict():
    h = H.assess(trades=300, mean=-0.005, se=0.0005, days=1)
    assert h["state"] != H.DISABLED and any("day(s) of record" in r for r in h["reasons"])
    assert H.assess(trades=300, mean=-0.005, se=0.0005, days=40)["state"] == H.DISABLED


# ------------------------------------------------------------------ counterfactual learning
def test_a_rejected_group_that_keeps_winning_is_flagged_and_noise_is_not():
    from financial_brain.opportunity import learning as L
    rng = np.random.default_rng(0)
    rows = []
    for s in range(60):
        for i in range(100):
            adv = rng.uniform(1, 100)
            win = 0.02 if adv > 80 else 0.0                     # the largest-turnover quintile wins
            rows.append({"session": s, "lineage": f"L{i}", "disposition": "REJECTED_FILTER", "reason": "REJECT_COST",
                         "fwd_excess": win + rng.normal(0, 0.01), "adv20": adv, "vol_60": rng.uniform(.1, .5),
                         "mom_12_1": rng.normal(0, .3)})
    df = pd.DataFrame(rows)
    df["fwd_excess"] -= df.groupby("session")["fwd_excess"].transform("mean")   # excess over the universe, as stored
    out = L.systematic_misses(df)
    f = out["flagged"]
    assert len(f) >= 1 and (f["dimension"] == "size").all() and (f["quintile"] == 5).all()
    assert out["groups_examined"] == 15 and "by chance" in out["chance_note"]


# ------------------------------------------------------------------ exposure graph
def test_exposures_find_the_home_sector_and_a_shock_propagates_through_it():
    from financial_brain.graph import exposure as X
    rng = np.random.default_rng(0)
    n = 300
    mkt = rng.normal(0, .01, n)
    metal = 0.9 * mkt + rng.normal(0, .012, n)
    it = 0.8 * mkt + rng.normal(0, .010, n)
    f = pd.DataFrame({X.MARKET: mkt, "Nifty Metal": metal, "Nifty IT": it})
    metal_res = metal - 0.9 * mkt
    s = pd.DataFrame({"STEEL": 1.2 * mkt + 1.0 * metal_res + rng.normal(0, .004, n),
                      "SOFT": 0.9 * mkt + rng.normal(0, .004, n) + 0.9 * (it - 0.8 * mkt)})
    ex, resid = X.build_from_returns(s, f)
    e = ex.set_index("ticker")
    assert e.loc["STEEL", "home_sector"] == "Nifty Metal" and e.loc["SOFT", "home_sector"] == "Nifty IT"
    assert e.loc["STEEL", "beta_market"] == pytest.approx(1.2 + 0.0, abs=0.25)
    p = X.propagate(ex, {X.MARKET: -0.01, "Nifty Metal": -0.03}).set_index("ticker")
    assert p.loc["STEEL", "implied"] < p.loc["SOFT", "implied"] < 0     # metal takes the extra hit
    peers = X.propagate_stock(resid, "STEEL", 0.08, k=1)
    assert len(peers) == 1
    with pytest.raises(X.ExposureError):
        X.propagate_stock(resid, "NOPE", 0.1)
