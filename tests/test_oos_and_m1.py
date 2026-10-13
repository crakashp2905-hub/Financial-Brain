"""G5 monitor and the M1 book's pieces (no database)."""
import numpy as np
import pandas as pd

from financial_brain.evaluation import momentum_book as MB
from financial_brain.evaluation import oos_monitor as OM

REG = OM.Registration("s", "p", "2026-10-12", 20, "unused")


def _bt(mean=0.01, sd=0.03, n=60, seed=0):
    rng = np.random.default_rng(seed)
    return pd.Series(mean + sd * rng.standard_normal(n), index=pd.date_range("2022-01-31", periods=n, freq="ME"))


def _live(per_session, n, start="2026-10-13"):
    days = pd.bdate_range(start, periods=n + 1)
    return pd.DataFrame({"day": days, "net": per_session, "bench": 0.0})


def test_pending_until_twelve_rebalances_then_confirmed():
    r = OM.assess(_live(0.0005, 100), _bt(), REG)
    assert r["verdict"] == OM.PENDING and r["rebalances"] == 5
    r = OM.assess(_live(0.0005, 240), _bt(), REG)
    assert r["verdict"] == OM.CONFIRMED


def test_three_month_ends_below_the_band_fail_early():
    r = OM.assess(_live(-0.01, 80), _bt(), REG)
    assert r["verdict"] == OM.FAILED_EARLY
    assert [m["where"] for m in r["months"][-3:]] == ["below"] * 3


def test_rows_before_the_cutoff_are_not_live():
    live = pd.concat([_live(0.5, 20, start="2026-09-01"), _live(0.0, 5)])
    r = OM.assess(live, _bt(), REG)
    assert r["sessions"] == 5 and abs(r["cum"]) < 1e-12


def test_choose_keeps_held_names_inside_the_band():
    score = pd.Series(np.linspace(1, 0, 200), index=[f"n{i}" for i in range(200)])
    held = ["n90", "n150"] + [f"n{i}" for i in range(38)]          # n90 inside top 100, n150 outside
    book = MB.choose(score, held)
    assert "n90" in book and "n150" not in book and len(book) == 40


def test_inverse_vol_caps_and_sums_to_one():
    vol = pd.Series([0.001] + [0.03] * 39, index=[f"n{i}" for i in range(40)])
    w = MB.inverse_vol(vol)
    assert abs(w.sum() - 1) < 1e-9 and w.max() <= MB.CAP + 1e-9
