from __future__ import annotations

from datetime import date

import duckdb

from financial_brain.features.etf_prices import build


def _fixture_db():
    con = duckdb.connect(":memory:")
    con.execute("""CREATE TABLE eod_prices (
        business_date DATE, exchange VARCHAR, isin VARCHAR, ticker VARCHAR,
        series VARCHAR, instrument_id VARCHAR, instrument_type VARCHAR,
        open_price DOUBLE, high_price DOUBLE, low_price DOUBLE, close_price DOUBLE,
        traded_volume BIGINT, turnover DOUBLE, evidence_key VARCHAR)""")
    con.execute("""CREATE TABLE lake_manifest (key VARCHAR, sha256 VARCHAR)""")
    con.execute("""CREATE TABLE universe_snapshots (
        business_date DATE, exchange VARCHAR, isin VARCHAR, series VARCHAR,
        instrument_type VARCHAR, tradable BOOLEAN)""")
    con.execute("""CREATE TABLE adjustment_factors (
        isin VARCHAR, effective_from DATE, price_factor DOUBLE)""")
    return con


def test_etf_daily_prices_reuse_hashed_eod_rows_and_adjustment_history() -> None:
    con = _fixture_db()
    isin = "INE000A01001"
    first, split_day = date(2021, 12, 31), date(2022, 1, 3)
    sha = "a" * 64
    for d, key, instrument_type in ((first, "lake://nse/a", "ETF"),
                                    (split_day, "lake://nse/b", "ETF"),
                                    (first, "lake://nse/stock", "STK")):
        con.execute(
            "INSERT INTO eod_prices VALUES (?, 'NSE', ?, ?, 'EQ', 'token', ?, 99, 105, 95, 100, 1000, 100000, ?)",
            [d, isin if instrument_type == "ETF" else "INE000A01019",
             "INDEXETF" if instrument_type == "ETF" else "STOCK", instrument_type, key],
        )
        con.execute(
            "INSERT INTO universe_snapshots VALUES (?, 'NSE', ?, 'EQ', ?, TRUE)",
            [d, isin if instrument_type == "ETF" else "INE000A01019", instrument_type],
        )
    for key in ("lake://nse/a", "lake://nse/b", "lake://nse/stock"):
        con.execute("INSERT INTO lake_manifest VALUES (?, ?)", [key, sha])
    con.execute("INSERT INTO adjustment_factors VALUES (?, ?, 0.5)", [isin, split_day])

    result = build(con)
    rows = con.execute(
        "SELECT business_date, close_raw, close_adjusted, source_sha256 FROM etf_daily_prices ORDER BY business_date"
    ).fetchall()
    assert result == {"rows": 2, "source_payloads": 1}
    assert rows == [(first, 100.0, 50.0, sha), (split_day, 100.0, 100.0, sha)]
    con.close()


def test_etf_daily_prices_omit_rows_without_manifest_hash() -> None:
    con = _fixture_db()
    d, isin = date(2023, 2, 1), "INE000A01001"
    con.execute(
        "INSERT INTO eod_prices VALUES (?, 'NSE', ?, 'ETF', 'EQ', 'token', 'ETF', 9, 11, 8, 10, 1, 10, 'missing')",
        [d, isin],
    )
    con.execute("INSERT INTO universe_snapshots VALUES (?, 'NSE', ?, 'EQ', 'ETF', TRUE)", [d, isin])
    assert build(con) == {"rows": 0, "source_payloads": 0}
    con.close()
