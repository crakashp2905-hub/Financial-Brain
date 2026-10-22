"""After-close option OI must never see a same-day or holdout outcome."""
from __future__ import annotations

from datetime import date

import duckdb
import pandas as pd
import pytest

from financial_brain.evaluation import oi_positioning as OI


def test_after_close_signal_enters_next_open_and_uses_prior_thresholds():
    days = pd.bdate_range("2020-01-01", periods=40)
    ref = pd.DataFrame({"trade_date": days, "open": [100 + i for i in range(40)],
                        "close": [101 + i for i in range(40)]})
    oi = pd.DataFrame({"trade_date": days, "put_oi": [100.0] * 21 + [1000.0] + [100.0] * 18,
                       "call_oi": [100.0] * 40})
    x = OI.observations(ref, oi, start=date(2020, 1, 1), end=date(2020, 2, 25), min_history=20)
    signal = x[x["trade_date"] == days[21]].iloc[0]
    assert signal["condition"] == "high_put_call_oi"
    assert signal["prior_high"] == 1.0
    assert signal["next_open"] == ref["open"].iloc[22]
    assert signal["next_session_return"] == pytest.approx(ref["close"].iloc[22] / ref["open"].iloc[22] - 1)
    assert signal["five_session_return"] == pytest.approx(ref["close"].iloc[26] / ref["open"].iloc[22] - 1)
    assert x["trade_date"].max() <= days[-6]


def test_holdout_and_duplicate_dates_fail_closed():
    con = duckdb.connect()
    with pytest.raises(ValueError, match="sealed"):
        OI.index_reference(con, end=date(2022, 1, 1))
    ref = pd.DataFrame({"trade_date": ["2021-12-30", "2022-01-03"],
                        "open": [100, 101], "close": [100, 101]})
    with pytest.raises(ValueError, match="training"):
        OI.observations(ref, pd.DataFrame(columns=["trade_date", "put_oi", "call_oi"]))
    ref = pd.DataFrame({"trade_date": ["2020-01-01", "2020-01-01"],
                        "open": [100, 101], "close": [100, 101]})
    with pytest.raises(ValueError, match="duplicate"):
        OI.observations(ref, pd.DataFrame(columns=["trade_date", "put_oi", "call_oi"]))


def test_oi_query_uses_same_day_moneyness_and_expiry_window():
    con = duckdb.connect()
    con.execute("""CREATE TABLE fo_bhav (trade_date DATE, instrument VARCHAR, symbol VARCHAR,
        expiry DATE, strike DOUBLE, option_type VARCHAR, oi DOUBLE, contracts DOUBLE)""")
    ref = pd.DataFrame({"trade_date": [date(2020, 1, 2)], "open": [100], "close": [100]})
    rows = [(date(2020, 1, 2), "OPTIDX", "NIFTY", date(2020, 1, 30), 100,
             kind, volume, 10) for kind, volume in (("CE", 50), ("PE", 100))]
    rows += [(date(2020, 1, 2), "OPTIDX", "NIFTY", date(2020, 1, 30), 150,
              "PE", 10000, 10)]
    con.executemany("INSERT INTO fo_bhav VALUES (?,?,?,?,?,?,?,?)", rows)
    result = OI.option_oi(con, ref, end=date(2020, 1, 31)).iloc[0]
    assert result["put_oi"] == 100 and result["call_oi"] == 50
