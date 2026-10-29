"""Matched-mode diagnostics never mistake different dates for a paired experiment."""
from datetime import date

import duckdb

from financial_brain.paper import reconcile


def _ledger():
    con = duckdb.connect()
    con.execute("""CREATE TABLE trades (
        day VARCHAR, mode VARCHAR, strategy VARCHAR, symbol VARCHAR, side VARCHAR,
        qty INTEGER, entry_ts VARCHAR, exit_ts VARCHAR, entry_px DOUBLE, exit_px DOUBLE,
        gross_pnl DOUBLE, costs DOUBLE, net_pnl DOUBLE)""")
    return con


def _row(con, day, mode, symbol, net, *, side="long"):
    con.execute("INSERT INTO trades VALUES (?,?,?,?,?,10,'09:40','15:15',100,101,?,?,?)",
                [day, mode, "ORB", symbol, side, net + 2, 2, net])


def test_only_exact_same_day_strategy_symbol_side_is_paired():
    con = _ledger()
    _row(con, "2026-10-28", "live", "ABC", 8)
    _row(con, "2026-10-28", "replay", "ABC", 3)
    _row(con, "2026-10-28", "live", "XYZ", -5)
    _row(con, "2026-10-27", "replay", "XYZ", 5)
    result = reconcile.compare(con)
    assert result["status"] == "PAIRED_DIAGNOSTIC"
    assert result["counts"] == {"paired": 1, "live_only": 1, "replay_only": 1}
    assert result["pairs"][0]["net_gap_bp"] == 50
    assert result["same_days"] == ["2026-10-28"]
    assert reconcile.compare(con, start=date(2026, 10, 28))["replay_days"] == 1
    con.close()


def test_unpaired_days_and_duplicate_keys_cannot_get_a_gap():
    con = _ledger()
    _row(con, "2026-10-28", "live", "ABC", 8)
    _row(con, "2026-10-27", "replay", "ABC", 3)
    result = reconcile.compare(con)
    assert result["status"] == "NO_PAIRED_TRADES"
    assert result["same_days"] == []
    assert result["mean_net_gap_bp"] is None
    _row(con, "2026-10-28", "replay", "ABC", 3)
    _row(con, "2026-10-28", "replay", "ABC", 4)
    result = reconcile.compare(con)
    assert result["counts"]["ambiguous_keys"] == 1
    assert result["mean_net_gap_bp"] is None
    con.close()
