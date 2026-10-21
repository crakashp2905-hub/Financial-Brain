"""Guardrails for observed-close options exploration."""
from __future__ import annotations

import duckdb
import pandas as pd
import pytest

from financial_brain.evaluation import options_real as OR


def test_training_boundary_refuses_holdout():
    con = duckdb.connect()
    with pytest.raises(ValueError, match="sealed"):
        OR.load_observed(con, end="2022-01-01")
    with pytest.raises(ValueError, match="sealed"):
        OR.load_observed(con, end="2021-12-31", start="2022-01-01")


def test_call_put_and_reversal_have_separate_labels_and_costs():
    rows = pd.DataFrame([
        {"signal_date": "2020-01-01", "spot": 105, "spot_5_ago": 100,
         "option_type": "CE", "entry_close": 10, "exit_close": 11, "executable_proxy": True},
        {"signal_date": "2020-01-01", "spot": 105, "spot_5_ago": 100,
         "option_type": "PE", "entry_close": 10, "exit_close": 11, "executable_proxy": True},
        {"signal_date": "2020-01-02", "spot": 95, "spot_5_ago": 100,
         "option_type": "PE", "entry_close": 10, "exit_close": 11, "executable_proxy": True},
    ])
    out = OR.candidates(rows)
    assert list(out["family"]) == ["five_day_trend", "five_day_reversal", "five_day_trend"]
    assert (out["net_proxy"] < 0.1).all()
    assert (out["net_proxy"] > 0).all()


def test_missing_exit_is_coverage_failure_not_profitable_trade():
    rows = pd.DataFrame([
        {"signal_date": "2020-01-01", "spot": 105, "spot_5_ago": 100,
         "option_type": "CE", "entry_close": 10, "exit_close": 11, "executable_proxy": True},
        {"signal_date": "2020-01-02", "spot": 105, "spot_5_ago": 100,
         "option_type": "CE", "entry_close": 10, "exit_close": None, "executable_proxy": False},
    ])
    out = OR.candidates(rows)
    report = OR.diagnostic(out)[0]
    assert report["signals"] == 2
    assert report["priced_exits"] == 1
    assert report["coverage"] == 0.5


def test_no_same_day_round_trip_in_source_query():
    con = duckdb.connect()
    con.execute("""CREATE TABLE fo_bhav (
        trade_date DATE, instrument VARCHAR, symbol VARCHAR, expiry DATE, strike DOUBLE,
        option_type VARCHAR, close DOUBLE, contracts DOUBLE)""")
    days = pd.bdate_range("2020-01-01", periods=20).date
    records = []
    for i, d in enumerate(days):
        records.append((d, "FUTIDX", "NIFTY", "2020-03-31", 0, "XX", 100 + i, 500))
        for kind in ("CE", "PE"):
            records.append((d, "OPTIDX", "NIFTY", "2020-03-31", 100, kind, 10 + i, 500))
    con.executemany("INSERT INTO fo_bhav VALUES (?, ?, ?, ?, ?, ?, ?, ?)", records)
    observed = OR.load_observed(con, start="2020-01-01", end="2020-01-31")
    assert not observed.empty
    assert (observed["entry_date"] > observed["signal_date"]).all()
    assert (observed["exit_date"] > observed["entry_date"]).all()
    reference = pd.DataFrame({"trade_date": days, "spot": [120 - i for i in range(len(days))]})
    with_index = OR.load_observed(con, reference=reference, start="2020-01-01", end="2020-01-31")
    assert not with_index.empty
    assert (with_index["spot"] < with_index["spot_5_ago"]).all()
