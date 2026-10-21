"""Edge candidates: the registered bar, calendar windows, cost of turnover, the index intraday rule."""

import numpy as np
import pandas as pd
import pytest

from financial_brain.evaluation import edges as E


def test_judge_needs_t2_in_both_periods():
    idx = pd.date_range("2016-01-31", "2026-06-30", freq="ME")
    rng = np.random.default_rng(0)
    s = pd.Series(0.01 + rng.normal(0, 0.01, len(idx)), index=idx)
    assert E.judge(s)["pass"]
    s2 = s.copy()
    s2[s2.index >= pd.Timestamp("2022-01-01")] = rng.normal(0, 0.01, (s2.index >= pd.Timestamp("2022-01-01")).sum())
    assert not E.judge(s2)["pass"]


def test_turn_of_month_window_and_pre_holiday_events():
    idx = pd.bdate_range("2024-01-01", "2024-03-29")
    idx = idx.drop(pd.Timestamp("2024-01-26"))                       # a holiday on a weekday
    tr = pd.Series(np.linspace(100, 110, len(idx)), index=idx)
    tom = E.turn_of_month(tr)
    a = tom.index[0]
    assert a == idx[idx.to_period("M") == pd.Period("2024-01", "M")][-3]       # two sessions before the last
    ph = E.pre_holiday(tr)
    assert list(ph.index) == [pd.Timestamp("2024-01-25")]
    b = pd.Timestamp("2024-01-29")
    assert ph["net"].iloc[0] == pytest.approx(tr[b] / tr[a.replace(day=25)] - 1 - E.RF * 4 / 365 - E.INDEX_RT)


def test_selection_charges_only_the_weight_that_changes():
    idx = pd.bdate_range("2024-01-01", periods=30)
    C = pd.DataFrame({"A": np.linspace(100, 130, 30), "B": 100.0, "C": np.linspace(100, 90, 30)}, index=idx)
    U = pd.DataFrame(True, index=idx, columns=C.columns)
    cost = pd.DataFrame(0.003, index=idx, columns=C.columns)
    P = {"C": C, "U": U, "cost": cost}
    s = E.selection_series(P, {idx[0]: ["A"], idx[5]: ["A"]}, 5)
    assert s["cost"].iloc[0] == pytest.approx(0.0015)                  # bought in: half a round trip
    assert s["cost"].iloc[1] == pytest.approx(0.0)                     # same name kept: nothing traded
    assert s["bench"].iloc[0] == pytest.approx(float((C.iloc[5] / C.iloc[0] - 1).mean()))


def test_selected_missing_exit_is_not_silently_dropped():
    idx = pd.bdate_range("2020-01-01", periods=10)
    C = pd.DataFrame({"A": [100.0] * 5 + [np.nan] * 5,
                      "B": [100.0] * 10}, index=idx)
    P = {"C": C, "U": pd.DataFrame(True, index=idx, columns=C.columns),
         "cost": pd.DataFrame(0.003, index=idx, columns=C.columns)}
    with pytest.raises(ValueError, match="unresolved future return"):
        E.selection_series(P, {idx[0]: ["A", "B"]}, 5)
    with pytest.raises(ValueError, match="unresolved future return"):
        E.monthly_selection(P, {idx[0]: ["A", "B"], idx[5]: ["B"]})


def test_index_intraday_momentum_takes_the_first_half_hours_sign():
    ts = pd.date_range("2024-01-02 09:15", "2024-01-02 15:29", freq="min", tz="Asia/Kolkata")
    close = pd.Series(100.0, index=ts)
    close[ts.strftime("%H:%M") >= "09:45"] = 101.0                     # up in the morning
    close[ts.strftime("%H:%M") >= "15:10"] = 101.5                     # and up into the close
    bars = pd.DataFrame({"ts": ts, "close": close.to_numpy()})
    out = E.index_intraday_momentum(bars, {pd.Timestamp("2024-01-02"): 100.0})
    r = out.iloc[0]
    assert r["gross"] == pytest.approx(101.5 / 101.0 - 1) and r["net"] == pytest.approx(r["gross"] - E.INDEX_RT)
