import numpy as np
import pandas as pd
import pytest

from financial_brain.evaluation import swing as SW


def world(T=400, N=120, seed=0, planted=0.0):
    rng = np.random.default_rng(seed)
    r = rng.normal(0.0003, 0.012, (T, N))
    sig = rng.random((T, N)) < 0.03
    if planted:
        for t, n in zip(*np.nonzero(sig)):
            if t + 8 < T:
                r[t + 2:t + 7, n] += planted            # five days of drift after the signal, from t+2
    P = pd.DataFrame(100 * np.cumprod(1 + r, axis=0), index=pd.bdate_range("2018-01-01", periods=T),
                     columns=[f"L{i}" for i in range(N)])
    ones = pd.DataFrame(1.0, index=P.index, columns=P.columns)
    d = {"P": P, "above_ma200": ones, "above_ma50": ones, "mom_12_1": ones * 0.1, "ret_5d": ones * 0.0,
         "rsi_14": ones * 50, "dist_52w_high": ones * -0.5, "adx_14": ones * 10, "bb_pct_20": ones * 0.5,
         "atr_14_pct": ones * 0.012, "vol_60": ones * 0.2, "adv20": ones * 5e8}
    d["rsi_14"] = d["rsi_14"].mask(pd.DataFrame(sig, index=P.index, columns=P.columns), 20.0)   # SW2 fires on `sig`
    return d, sig


def test_cost_is_fixed_fees_plus_impact_that_grows_with_size_over_turnover():
    c1 = SW.sized_cost(np.array([0.2]), np.array([5e8]))
    c2 = SW.sized_cost(np.array([0.2]), np.array([2e7]))
    assert c2 > c1 > SW.fee_fraction() > 0.002


def test_entry_is_one_day_after_the_signal():
    d, sig = world(planted=0.02)
    res = SW.run(d, horizons=(5,))
    r = res[("SW2 oversold in uptrend", 5)]
    # the planted drift starts at t+2: a trade entered at the t+1 close and held 5 days captures it
    assert r["gross"]["mean"] > 0.05
    # a signal on a *different* rule (SW1: never true here) produces no trades at all
    assert ("SW1 pullback in uptrend", 5) not in res


def test_without_an_edge_the_net_result_is_negative_by_about_the_cost():
    d, _ = world(planted=0.0)
    r = SW.run(d, horizons=(5,))[("SW2 oversold in uptrend", 5)]
    assert abs(r["gross"]["mean"]) < 0.01 and r["net"]["mean"] < r["gross"]["mean"] - 0.002


def test_the_stop_fires_on_a_fall_and_reports_its_share():
    d, sig = world(planted=0.0, seed=3)
    r = SW.run(d, horizons=(5,), stop_k=0.5)[("SW2 oversold in uptrend", 5)]
    assert 0.05 < r["stopped"] < 0.95
    wide = SW.run(d, horizons=(5,), stop_k=20.0)[("SW2 oversold in uptrend", 5)]
    assert wide["stopped"] == 0.0 and wide["net_stop"]["mean"] == pytest.approx(wide["net"]["mean"], abs=1e-12)


def test_date_clustering_needs_enough_dates():
    out = SW.date_clustered(np.arange(10.0), np.array(pd.date_range("2020-01-01", periods=10)))
    assert np.isnan(out["t"]) and out["trades"] == 10


def test_load_turns_nullable_booleans_into_floats(tmp_path):
    import duckdb
    from datetime import date
    c = duckdb.connect()
    cols = ", ".join(f"{k} DOUBLE" for k in SW.COLUMNS if k not in ("above_ma200", "above_ma50"))
    c.execute(f"CREATE TABLE features(lineage VARCHAR, business_date DATE, above_ma200 BOOLEAN, above_ma50 BOOLEAN, {cols})")
    c.execute("CREATE TABLE adjusted_prices(lineage VARCHAR, business_date DATE, close_adj DOUBLE)")
    days = [date(2020, 1, d) for d in (1, 2, 3, 6, 7)]
    for i, d in enumerate(days):
        c.execute("INSERT INTO adjusted_prices VALUES ('A', ?, ?)", [d, 100 + i])
        vals = ["TRUE" if i % 2 else "NULL", "TRUE"] + ["1.0"] * (len(SW.COLUMNS) - 2)
        c.execute(f"INSERT INTO features VALUES ('A', ?, {', '.join(vals)})", [d])
    d = SW.load(c, date(2020, 1, 1), date(2020, 1, 31))
    assert str(d["above_ma200"].dtypes.iloc[0]) == "float64" and d["above_ma200"].isna().any().any()
    mask = SW.SETUPS["SW2 oversold in uptrend"](d)            # must not raise on the NaNs
    assert mask.dtypes.iloc[0] == bool


def test_a_skewed_universe_makes_a_random_setup_look_good_against_the_median_but_not_the_mean():
    rng = np.random.default_rng(5)
    T, N = 500, 150
    r = np.exp(rng.normal(0, 0.03, (T, N))) - 1.0 + 0.0002          # right-skewed daily returns
    P = pd.DataFrame(100 * np.cumprod(1 + r, axis=0), index=pd.bdate_range("2018-01-01", periods=T),
                     columns=[f"L{i}" for i in range(N)])
    ones = pd.DataFrame(1.0, index=P.index, columns=P.columns)
    d = {"P": P, "above_ma200": ones, "above_ma50": ones, "mom_12_1": ones * .1, "ret_5d": ones * 0.0,
         "rsi_14": ones * 20, "dist_52w_high": ones * -.5, "adx_14": ones * 10, "bb_pct_20": ones * .5,
         "atr_14_pct": ones * .02, "vol_60": ones * .3, "adv20": ones * 5e8}       # SW2 fires on every name
    res = SW.run(d, horizons=(10,))[("SW2 oversold in uptrend", 10)]
    assert res["gross"]["mean"] > 0.002                  # against the median: an apparent edge from skew alone
    assert abs(res["gross_vs_mean"]["mean"]) < 1e-9      # against the mean: exactly none, as it should be
    assert res["skew_gap"] > 0.002
