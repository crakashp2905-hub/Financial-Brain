from datetime import date

import numpy as np
import pandas as pd
import pytest

from financial_brain.evaluation import earnings_reaction as ER
from financial_brain.evaluation import setup_analogues as SA


def pool(n_days=200, n=60, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for k, d in enumerate(pd.bdate_range("2018-01-01", periods=n_days)):  # noqa: B007
        for i in range(n):
            f = rng.normal(0, 1, len(SA.FEATURES))
            x5 = 0.01 * f[0] + rng.normal(0, 0.02)          # the first feature predicts the 5-day outcome
            rows.append({"business_date": d.date(), "ticker": f"T{i}", **dict(zip(SA.FEATURES, f)),
                         **{f"x{h}": x5 if h == 5 else rng.normal(0, .02) for h in SA.HORIZONS},
                         "mfe5": abs(rng.normal(.03, .01)), "mae5": -abs(rng.normal(.03, .01))})
    return pd.DataFrame(rows)


def test_analogues_of_a_high_first_feature_beat_the_pool_and_the_pool_is_reported():
    p = pool()
    x = np.zeros(len(SA.FEATURES))
    x[0] = 2.0
    out = SA.query(p, x, date(2019, 1, 1))
    assert out["k"] == SA.K and out["h5"]["edge"] > 0.005 and out["h5"]["edge_t"] > 3
    assert 0.4 < out["h1"]["pool_win_rate"] < 0.6 and out["mfe5_median"] > 0 > out["mae5_median"]
    assert out["positive"] + out["negative"] == SA.K and out["best"]["x5"] >= out["worst"]["x5"]
    assert out["best"]["ticker"].startswith("T") and out["best"]["date"] < "2019-01-01"


def test_only_closed_windows_before_the_date_are_eligible():
    p = pool()
    with pytest.raises(SA.AnalogueError):
        SA.query(p, np.zeros(len(SA.FEATURES)), date(2018, 1, 20))


def test_target_is_ranked_within_its_own_cross_section():
    cs = pd.DataFrame({"ticker": [f"T{i}" for i in range(50)], **{f: np.arange(50.0) for f in SA.FEATURES}})
    hi = SA.target_vector(cs, "T49")
    lo = SA.target_vector(cs, "T0")
    assert (hi > 1.5).all() and (lo < -1.5).all()


def test_sue_is_measured_at_filing_with_only_what_was_known():
    rows = []
    eps = [1, 1, 1, 1, 1.1, 1.0, 1.2, 1.1, 1.05, 1.15, 3.0]
    ends = pd.date_range("2019-03-31", periods=len(eps), freq="QE")
    for e, v in zip(ends, eps):
        rows.append({"key": "A", "period_end": e, "filed_at": e + pd.Timedelta(days=40), "revenue": 100.0,
                     "pat": 10.0, "eps": v, "basis": "consolidated"})
    s = ER.sue_at_filing(pd.DataFrame(rows))
    assert len(s) >= 1 and s.iloc[-1]["sue"] > 3 and s.iloc[-1]["period_end"] == ends[-1]


def test_reaction_uses_the_next_session_for_an_after_close_filing():
    idx = pd.bdate_range("2024-01-01", periods=40)
    w = pd.DataFrame({"A": 100.0, "B": 100.0}, index=idx)
    w.loc[idx[11]:, "A"] = 110.0                         # A jumps on the 12th session
    ev = pd.DataFrame({"key": ["A"], "filed_at": [pd.Timestamp(idx[10]) + pd.Timedelta(hours=18)], "sue": [2.0]})
    r = ER.reactions(ev, w)
    assert r.iloc[0]["react"] == pytest.approx(0.10 - 0.05)    # minus the universe mean move
    assert abs(r.iloc[0]["d5"]) < 1e-12


def test_quintiles_need_enough_events():
    with pytest.raises(ER.ReactionError):
        ER.by_quintile(pd.DataFrame({"sue": [1.0] * 10, "react": 0.0, "d5": 0.0, "d20": 0.0}))
