"""Malformed, non-finite market numbers are quarantined before curation."""
from datetime import date

import duckdb
import pytest

from financial_brain.ingest import quality
from financial_brain.providers.bhavcopy import UDIFF_COLUMNS


@pytest.mark.parametrize("field,value", [
    ("HghPric", "NaN"), ("ClsPric", "not-a-price"),
    ("OpnPric", "Infinity"), ("TtlTradgVol", "1.5"),
])
def test_malformed_nonblank_numeric_row_is_quarantined(field, value):
    con = duckdb.connect()
    con.execute("CREATE TABLE raw (" + ", ".join(f"{column} VARCHAR" for column in UDIFF_COLUMNS) + ")")
    base = {column: "" for column in UDIFF_COLUMNS}
    base.update(TradDt="2026-10-30", FinInstrmTp="STK", ISIN="INE000000019",
                FinInstrmId="1", TckrSymb="GOOD", SctySrs="EQ",
                OpnPric="100", HghPric="110", LwPric="95", ClsPric="105", TtlTradgVol="1000")
    con.execute("INSERT INTO raw VALUES (" + ",".join("?" for _ in UDIFF_COLUMNS) + ")",
                [base[column] for column in UDIFF_COLUMNS])
    base.update(ISIN="INE000000027", FinInstrmId="2", TckrSymb="BAD")
    base[field] = value
    con.execute("INSERT INTO raw VALUES (" + ",".join("?" for _ in UDIFF_COLUMNS) + ")",
                [base[column] for column in UDIFF_COLUMNS])
    report = quality.check_bhavcopy(con, "raw", business_date=date(2026, 10, 30), min_rows=1)
    assert report.publishable
    assert any(row.name == "numeric_fields_well_formed" for row in report.row_level)
    assert quality.partition_rows(con, "raw", report) == 1
    assert con.execute("SELECT TckrSymb FROM raw_clean").fetchall() == [("GOOD",)]
    assert con.execute("SELECT reject_reason FROM raw_rejected").fetchone()[0] == "numeric_fields_well_formed"


@pytest.mark.parametrize("open_price", ["94", "111"])
def test_open_outside_high_low_is_quarantined(open_price):
    con = duckdb.connect()
    con.execute("CREATE TABLE raw (" + ", ".join(f"{column} VARCHAR" for column in UDIFF_COLUMNS) + ")")
    row = {column: "" for column in UDIFF_COLUMNS}
    row.update(TradDt="2026-10-30", FinInstrmTp="STK", ISIN="INE000000019",
               FinInstrmId="1", TckrSymb="BAD", SctySrs="EQ",
               OpnPric=open_price, HghPric="110", LwPric="95", ClsPric="105",
               TtlTradgVol="1000")
    con.execute("INSERT INTO raw VALUES (" + ",".join("?" for _ in UDIFF_COLUMNS) + ")",
                [row[column] for column in UDIFF_COLUMNS])
    report = quality.check_bhavcopy(con, "raw", business_date=date(2026, 10, 30), min_rows=1)
    assert report.publishable
    assert any(result.name == "ohlc_coherent" for result in report.row_level)
    assert quality.partition_rows(con, "raw", report) == 1
    assert con.execute("SELECT reject_reason FROM raw_rejected").fetchone()[0] == "ohlc_coherent"
