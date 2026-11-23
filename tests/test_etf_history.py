from __future__ import annotations

from datetime import date

import duckdb
import pytest

from financial_brain.etf_history import ETFHistoryUnavailable, history


def _db():
    con = duckdb.connect(":memory:")
    con.execute("""CREATE TABLE etf_reference_observations
        (observed_on DATE, exchange VARCHAR, isin VARCHAR, listing_date DATE, status VARCHAR)""")
    con.execute("""CREATE TABLE eod_prices
        (business_date DATE, exchange VARCHAR, series VARCHAR, isin VARCHAR, ticker VARCHAR,
         open_price DOUBLE, high_price DOUBLE, low_price DOUBLE, close_price DOUBLE,
         traded_volume BIGINT, turnover DOUBLE, evidence_key VARCHAR)""")
    con.execute("""CREATE TABLE adjusted_prices
        (business_date DATE, isin VARCHAR, close_adj DOUBLE, factor DOUBLE)""")
    con.execute("""INSERT INTO etf_reference_observations VALUES
        ('2026-11-23','NSE','INF000A01010','2002-01-08','LISTED')""")
    con.execute("""INSERT INTO eod_prices VALUES
        ('2026-11-20','NSE','EQ','INF000A01010','NIFTYBEES',100,110,90,105,100,10500,'NSE/raw/20'),
        ('2026-11-23','NSE','EQ','INF000A01010','NIFTYBEES',50,55,45,52,200,10400,'NSE/raw/23')""")
    con.execute("""INSERT INTO adjusted_prices VALUES
        ('2026-11-20','INF000A01010',52.5,0.5),
        ('2026-11-23','INF000A01010',52,1.0)""")
    return con


def test_etf_history_joins_adjusted_ohlcv_to_hashed_raw_bars() -> None:
    con = _db()
    try:
        bars = history(con, "INF000A01010", date(2026, 11, 23))
    finally:
        con.close()
    assert [bar.trade_date for bar in bars] == [date(2026, 11, 20), date(2026, 11, 23)]
    assert bars[0].open == 50 and bars[0].high == 55 and bars[0].close == 52.5
    assert bars[0].volume == 200 and bars[0].evidence_key == "NSE/raw/20"
    assert bars[1].close == 52 and bars[1].evidence_key == "NSE/raw/23"


def test_etf_history_requires_asof_identity_and_available_adjusted_prices() -> None:
    con = _db()
    try:
        with pytest.raises(ETFHistoryUnavailable, match="reference"):
            history(con, "INF000A01010", date(2026, 11, 22))
        with pytest.raises(ETFHistoryUnavailable, match="reference"):
            history(con, "INF000A01028", date(2026, 11, 23))
        with pytest.raises(ValueError, match="positive"):
            history(con, "INF000A01010", date(2026, 11, 23), limit=0)
    finally:
        con.close()
