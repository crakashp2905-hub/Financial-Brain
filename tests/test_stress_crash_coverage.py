from datetime import date

import duckdb
import pytest

from financial_brain.risk import stress


def test_historical_crash_above_fifty_percent_is_not_filtered_out():
    con = duckdb.connect()
    con.execute("CREATE TABLE adjusted_prices(isin VARCHAR, business_date DATE, close_adj DOUBLE)")
    d0, d1 = date(2026, 10, 28), date(2026, 10, 29)
    con.executemany("INSERT INTO adjusted_prices VALUES (?,?,?)", [
        ("A", d0, 100), ("B", d0, 100), ("A", d1, 40), ("B", d1, 100),
    ])
    result = stress.book_returns(con, {"A": .5, "B": .5}, end=d1, sessions=2)
    assert result["returns"] == pytest.approx([-.3])
    assert result["coverage"] == [1.0]
