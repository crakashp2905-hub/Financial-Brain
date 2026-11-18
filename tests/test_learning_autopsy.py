"""Trade autopsies: each loss gets one primary cause from the path it actually took."""
from __future__ import annotations

from types import SimpleNamespace

import duckdb
import numpy as np
import pandas as pd
import pytest

from financial_brain.learning import autopsy as AU

DAY = "2026-11-02"


def _trade(**kw):
    base = dict(trade_id="t1", mode="live", day=DAY, symbol="AAA", strategy="ORB", side="long", qty=100, entry_px=100.0,
                stop=99.0, target=102.0, entry_ts=f"{DAY}T09:35:00+05:30", exit_ts=f"{DAY}T10:05:00+05:30",
                exit_reason="stop", gross_pnl=-100.0, costs=30.0, net_pnl=-130.0, spread_bp=3.0, shortfall_bp=2.0)
    base.update(kw)
    return SimpleNamespace(**base)


def _path(points: list[tuple[str, float]]):
    ts = np.array([np.datetime64(f"{DAY}T{t}") for t, _ in points], dtype="datetime64[ns]")
    return ts, np.array([p for _, p in points], dtype=float)


# a 1R is Rs 1 on a Rs 100 entry with a stop at 99
STOP_THEN_RALLY = _path([("09:35:00", 100.0), ("09:50:00", 99.0), ("10:05:00", 98.9), ("11:00:00", 101.2), ("13:00:00", 101.5)])
STOP_AND_STAY = _path([("09:35:00", 100.0), ("09:50:00", 99.5), ("10:05:00", 98.9), ("11:00:00", 98.0), ("13:00:00", 97.0)])
UP_THEN_STOPPED = _path([("09:35:00", 100.0), ("09:45:00", 100.5), ("10:05:00", 98.9), ("11:00:00", 98.0)])
UP_BIG_THEN_DOWN = _path([("09:35:00", 100.0), ("09:45:00", 101.6), ("10:04:00", 99.5), ("10:05:00", 99.2), ("11:00:00", 99.0)])


def test_a_stop_followed_by_a_rally_is_a_noise_stop():
    r = AU.classify(_trade(), STOP_THEN_RALLY)
    assert r["primary_class"] == "NOISE_STOP" and r["reversed_after_exit"] is True and r["has_path"]


def test_a_stop_that_never_worked_is_labelled_so():
    r = AU.classify(_trade(), STOP_AND_STAY)
    assert r["primary_class"] == "NEVER_WORKED" and r["mfe_r"] < AU.WORKED_R and r["reversed_after_exit"] is False


def test_a_stop_after_some_progress_is_an_ordinary_stop():
    r = AU.classify(_trade(), UP_THEN_STOPPED)
    assert r["primary_class"] == "STOPPED" and AU.WORKED_R <= r["mfe_r"] < AU.GAVE_BACK_R


def test_a_trade_that_was_well_up_and_lost_gave_it_back():
    r = AU.classify(_trade(exit_reason="time", exit_ts=f"{DAY}T10:05:00+05:30"), UP_BIG_THEN_DOWN)
    assert r["primary_class"] == "GAVE_BACK" and r["mfe_r"] >= 1.5


def test_a_profitable_signal_eaten_by_costs_is_cost_dominated_before_anything_else():
    r = AU.classify(_trade(gross_pnl=20.0, costs=60.0, net_pnl=-40.0), STOP_THEN_RALLY)
    assert r["primary_class"] == "COST_DOMINATED"


def test_a_winner_is_a_win_and_a_time_loss_is_a_time_loss():
    assert AU.classify(_trade(gross_pnl=200.0, net_pnl=150.0, exit_reason="target"), STOP_AND_STAY)["primary_class"] == "WIN"
    assert AU.classify(_trade(exit_reason="time"), STOP_AND_STAY)["primary_class"] == "TIME_LOSS"


def test_shorts_are_the_mirror_image():
    path = _path([("09:35:00", 100.0), ("09:50:00", 101.0), ("10:05:00", 101.1), ("11:00:00", 98.8)])
    r = AU.classify(_trade(side="short", stop=101.0, exit_reason="stop"), path)
    assert r["primary_class"] == "NOISE_STOP"


def test_without_a_path_only_path_free_causes_are_used_and_it_says_so():
    r = AU.classify(_trade(), None)
    assert r["primary_class"] == "STOPPED" and r["has_path"] is False and np.isnan(r["mfe_r"])
    assert AU.classify(_trade(gross_pnl=10.0, net_pnl=-5.0), None)["primary_class"] == "COST_DOMINATED"


def test_wide_spread_and_slippage_are_flags_beside_the_cause_not_causes():
    r = AU.classify(_trade(spread_bp=6.0, shortfall_bp=15.0), STOP_AND_STAY)
    assert r["primary_class"] == "NEVER_WORKED" and {"WIDE_SPREAD", "HIGH_SLIPPAGE"} <= set(r["classes"].split(","))


def test_build_reads_paths_from_the_archive_and_leaks_add_up():
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE quotes (ts TIMESTAMP, symbol VARCHAR, ltp DOUBLE)")
    for sym, path in (("AAA", STOP_THEN_RALLY), ("BBB", STOP_AND_STAY)):
        con.executemany("INSERT INTO quotes VALUES (?,?,?)", [(pd.Timestamp(t), sym, float(p)) for t, p in zip(*path, strict=True)])
    rows = [_trade(trade_id="a", symbol="AAA"), _trade(trade_id="b", symbol="BBB", net_pnl=-90.0),
            _trade(trade_id="c", symbol="CCC", net_pnl=300.0, gross_pnl=330.0, exit_reason="target")]
    auto = AU.build(pd.DataFrame([vars(r) for r in rows]), con)
    assert dict(zip(auto["trade_id"], auto["primary_class"], strict=True)) == {"a": "NOISE_STOP", "b": "NEVER_WORKED", "c": "WIN"}
    assert auto.set_index("trade_id").loc["c", "has_path"] == False                       # noqa: E712 - no quotes for CCC
    lk = AU.leaks(auto)
    assert lk["lost_inr"].sum() == pytest.approx(130.0 + 90.0) and lk["share"].sum() == pytest.approx(1.0)
    assert "primary cause" in AU.render(auto) and AU.summary(auto)["losers"] == 2


def test_no_trades_is_not_an_error():
    assert AU.build(pd.DataFrame()).empty and "no closed trades" in AU.render(pd.DataFrame())
