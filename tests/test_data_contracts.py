"""A contract that is not met is a failure: an empty table, or a last date past its allowed lag."""
from datetime import date, datetime

import duckdb

from financial_brain import data_contracts as DC


def test_lag_and_empty_tables_fail():
    con = duckdb.connect()
    for c in DC.CONTRACTS:
        kind = "TIMESTAMP" if c.date_col in ("filed_at", "disseminated_at") else "DATE"
        con.execute(f"CREATE TABLE {c.table} ({c.date_col} {kind})")
        if c.table != "delivery":
            con.execute(f"INSERT INTO {c.table} VALUES (?)", [datetime(2026, 10, 15, 18, 0)])
    con.execute("UPDATE eod_prices SET business_date = DATE '2026-10-01'")
    r = {x["table"]: x for x in DC.check(con, today=date(2026, 10, 16))}
    assert not r["eod_prices"]["ok"] and r["eod_prices"]["lag_days"] == 15
    assert not r["delivery"]["ok"] and r["delivery"]["why"] == "empty"
    assert r["financial_results"]["ok"] and r["insider_disclosures"]["ok"] and r["features"]["ok"]
