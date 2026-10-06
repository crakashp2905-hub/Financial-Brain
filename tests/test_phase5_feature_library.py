"""The deep feature library: no look-ahead anywhere, and the fair value gap rules as written."""
import numpy as np
import pandas as pd
import pytest

from financial_brain.features import library as LB


def bars(n=400, seed=0):
    rng = np.random.default_rng(seed)
    c = 100 * np.cumprod(1 + rng.normal(0.0005, 0.02, n))
    o = c * (1 + rng.normal(0, 0.006, n))
    h = np.maximum(o, c) * (1 + rng.uniform(0, 0.015, n))
    l = np.minimum(o, c) * (1 - rng.uniform(0, 0.015, n))
    return pd.DataFrame({"d": pd.bdate_range("2020-01-01", periods=n).date, "o": o, "h": h, "l": l, "c": c,
                         "v": rng.uniform(1e5, 1e6, n)})


def test_no_feature_looks_ahead():
    b = bars()
    full = LB.compute(b)
    cut = LB.compute(b.iloc[:300])
    cols = LB.feature_names(full)
    a, z = full.iloc[:300][cols].to_numpy(dtype=float), cut[cols].to_numpy(dtype=float)
    bad = [c for i, c in enumerate(cols) if not np.allclose(a[:, i], z[:, i], equal_nan=True)]
    assert bad == []                                   # every value at t is the same whether or not t+1.. exist
    assert len(cols) >= 100


def test_a_bullish_gap_is_found_retested_and_filled_in_order():
    rows = [(100, 101, 99, 100), (101, 103, 100.5, 102.5), (104, 106, 103.5, 105.5),   # gap: low 103.5 > high 101
            (105, 105.5, 102.0, 104.0),                                                  # enters gap, holds above 101: retest
            (104, 104.5, 100.5, 101.0)]                                                  # low 100.5 <= 101: filled
    b = pd.DataFrame(rows, columns=["o", "h", "l", "c"]).assign(d=pd.bdate_range("2024-01-01", periods=5).date, v=1e5)
    f = LB._fvg_features(*(b[k].to_numpy(dtype=float) for k in ("o", "h", "l", "c")), np.full(5, 1.0))
    assert f["fvg_bull_new"][2] == pytest.approx(2.5) and f["fvg_bull_open"][2] == 1
    assert f["fvg_bull_retest"][3] == 1.0 and f["fvg_bull_open"][3] == 1
    assert f["fvg_bull_open"][4] == 0                  # filled on bar 4, not before
    ev = LB.fvg_events(b)
    assert ev.iloc[0]["side"] == "bull" and ev.iloc[0]["filled_5"] and ev.iloc[0]["retest_d"] == b["d"].iloc[3]


def test_a_swing_is_used_only_once_confirmed():
    h = np.array([1, 2, 5, 2, 1, 1, 1, 6, 1.0])
    l = h - 0.5
    c = np.array([1, 2, 4, 2, 1, 1, 1, 6, 1.0])
    s = LB._structure(c, h, l, c, np.ones(9))
    assert s["bos_up"][7] == 1.0                       # close 6 breaks the swing high 5 (pivot at 2, confirmed at 4)
    assert s["bos_up"][2] == 0.0
