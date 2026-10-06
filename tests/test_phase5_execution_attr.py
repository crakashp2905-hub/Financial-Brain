"""Signal, execution and charges add up to the net P&L of every paper trade."""
from datetime import datetime

import pytest

from financial_brain.paper import execution as X
from financial_brain.paper import intraday as I
from financial_brain.paper import intraday_runner as R


def test_signal_execution_and_charges_sum_to_net(tmp_path):
    con = R.ledger(tmp_path / "l.duckdb")
    e = I.DayEngine("2026-10-06")
    e.add_symbol("A", 100.0)
    e.set_opening("A", open_px=100.0, or_hi=101.0, or_lo=99.0)
    for t in e.on_price("A", datetime(2026, 10, 6, 9, 32), 101.5) + e.on_price("A", datetime(2026, 10, 6, 10, 0), 102.0):
        R.save(con, t, "replay")
    for t in e.end_of_day(datetime(2026, 10, 6, 15, 20), {"A": 102.0}):
        R.save(con, t, "replay")
    r = X.attribution(con)[0]
    assert r["signal_inr"] + r["execution_inr"] + r["charges_inr"] == pytest.approx(r["net_inr"])
    assert r["execution_inr"] < 0 and r["charges_inr"] < 0          # a replay fill pays the assumed half-spread
    assert "execution" in X.render([r])
