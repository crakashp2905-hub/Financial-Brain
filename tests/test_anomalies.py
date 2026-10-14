"""Published-anomaly signals (trial 10): no look-ahead, definitions as registered."""
import numpy as np
import pandas as pd

from financial_brain.evaluation import anomalies as AN


def _panel(n=1100, k=30, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2015-01-01", periods=n)
    m = 0.01 * rng.standard_normal(n)
    R = pd.DataFrame(0.8 * m[:, None] + 0.015 * rng.standard_normal((n, k)), index=idx,
                     columns=[f"L{i}" for i in range(k)])
    R.iloc[::37, 3] = 0.0                                       # some zero-return days for one name
    C = 100 * (1 + R).cumprod()
    N = pd.Series(1000 * np.cumprod(1 + m), index=idx)
    V = pd.DataFrame(rng.integers(1_000, 100_000, (n, k)), index=idx, columns=C.columns).astype(float)
    return {"C": C, "R": C.pct_change(), "N": N, "RN": N.pct_change(), "V": V}


def _dates(P, upto=None):
    idx = P["C"].index if upto is None else P["C"].index[:upto]
    s = pd.Series(idx, index=idx)
    return list(s.groupby(idx.to_period("M")).max())[:-1]


def test_signals_use_no_later_data():
    P = _panel()
    full = AN.signals(P, _dates(P))
    cut = {k: v.iloc[:900] for k, v in P.items()}
    part = AN.signals(cut, _dates(cut))
    d = _dates(cut)[-1]
    for k in full:
        a, b = full[k].loc[d], part[k].loc[d]
        assert np.allclose(a.fillna(-9), b.fillna(-9)), k


def test_definitions():
    P = _panel()
    dates = _dates(P)
    s = AN.signals(P, dates)
    d = dates[-1]
    i = P["C"].index.get_loc(d)
    C = P["C"]
    assert np.allclose(s["A1_intermediate_mom"].loc[d], C.iloc[i - 147] / C.iloc[i - 252] - 1)
    assert np.allclose(s["A5_max"].loc[d], P["R"].iloc[i - 20:i + 1].max())
    assert s["A12_zero_days"].loc[d, "L3"] > s["A12_zero_days"].loc[d].drop("L3").max()
    beta_names = s["A8_downside_beta"].loc[d]
    assert beta_names.between(0.3, 1.4).all()                   # planted market beta 0.8
