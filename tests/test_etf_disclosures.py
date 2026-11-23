from __future__ import annotations

from datetime import date

import pytest

from financial_brain.config import Config
from financial_brain.ingest.reference import ingest_etf_product_disclosures
from financial_brain.providers.base import FetchError
from financial_brain.providers.etf_disclosures import parse
from financial_brain.storage.db import Database

_DOC_HASH = "a" * 64
_URL = "https://www.example-amc.in/downloads/etf-sid.pdf"
_CSV = (
    "isin,benchmark_name,benchmark_return_type,fund_house,inception_date,listing_date,"
    "nav,nav_as_of,indicative_nav,indicative_nav_at,expense_ratio_pct,"
    "expense_effective_from,expense_effective_to,tracking_error_pct,"
    "tracking_difference_pct,tracking_period_start,tracking_period_end,"
    "creation_units,redemption_units,creation_unit_size,flow_period_start,flow_period_end,"
    "source_url,source_document_sha256,"
    "source_published_at\n"
    f"INF000A01010,Nifty 50 TRI,TRI,Nippon India Mutual Fund,2002-01-08,2002-01-08,"
    f"251.25,2026-11-20,250.90,2026-11-23T10:15:00+05:30,0.25,2026-01-01,,"
    f"0.07,-0.01,2025-11-20,2026-11-20,2,1,50000,2026-10-01,2026-10-31,"
    f"{_URL},{_DOC_HASH},"
    "2026-11-20T18:00:00+05:30\n"
).encode()


def test_etf_disclosure_parser_preserves_source_backed_product_fields() -> None:
    row = parse(_CSV, date(2026, 11, 23))[0]
    assert row["isin"] == "INF000A01010"
    assert row["benchmark_name"] == "Nifty 50 TRI"
    assert row["benchmark_return_type"] == "TRI"
    assert row["fund_house"] == "Nippon India Mutual Fund"
    assert str(row["nav"]) == "251.25"
    assert str(row["indicative_nav"]) == "250.90"
    assert str(row["expense_ratio_pct"]) == "0.25"
    assert row["expense_effective_from"] == date(2026, 1, 1)
    assert str(row["tracking_error_pct"]) == "0.07"
    assert str(row["tracking_difference_pct"]) == "-0.01"
    assert row["tracking_period_start"] == date(2025, 11, 20)
    assert row["tracking_period_end"] == date(2026, 11, 20)
    assert row["creation_units"] == 2
    assert row["redemption_units"] == 1
    assert row["creation_unit_size"] == 50000
    assert row["flow_period_start"] == date(2026, 10, 1)
    assert row["flow_period_end"] == date(2026, 10, 31)
    assert row["source_url"] == _URL
    assert row["source_document_sha256"] == _DOC_HASH


@pytest.mark.parametrize(
    "change",
    [
        lambda text: text.replace(_DOC_HASH, "not-a-hash"),
        lambda text: text.replace("TRI,Nippon", "", 1),
        lambda text: text.replace("2026-11-23T10:15:00+05:30", "2026-11-23T10:15:00"),
        lambda text: text.replace("250.90,2026-11-23T10:15:00+05:30", ",2026-11-23T10:15:00+05:30"),
        lambda text: text.replace("0.25,2026-01-01,,", "-0.25,2026-01-01,,"),
        lambda text: text.replace("2025-11-20,2026-11-20", "2026-11-20,2025-11-20"),
        lambda text: text.replace(",2,1,50000,", ",-2,1,50000,"),
        lambda text: text.replace(",2,1,50000,", ",2,1,,"),
    ],
)
def test_etf_disclosure_parser_rejects_unverifiable_facts(change) -> None:
    with pytest.raises(FetchError):
        parse(change(_CSV.decode()).encode(), date(2026, 11, 23))


def test_etf_disclosure_import_is_archived_and_idempotent(tmp_path) -> None:
    cfg = Config(data_root=tmp_path).ensure()
    result = ingest_etf_product_disclosures(_CSV, date(2026, 11, 23), cfg)
    again = ingest_etf_product_disclosures(_CSV, date(2026, 11, 23), cfg)
    assert result == again
    with Database(cfg).connect() as con:
        rows = con.execute(
            """SELECT benchmark_name, benchmark_return_type, fund_house, nav,
                      indicative_nav, expense_ratio_pct, tracking_error_pct,
                      tracking_difference_pct, creation_units, redemption_units,
                      creation_unit_size, flow_period_start, flow_period_end,
                      source_url, source_document_sha256, lake_key
               FROM etf_product_disclosures"""
        ).fetchall()
    assert len(rows) == 1
    assert rows[0][0:3] == ("Nifty 50 TRI", "TRI", "Nippon India Mutual Fund")
    assert str(rows[0][3]) == "251.25000000"
    assert str(rows[0][4]) == "250.90000000"
    assert str(rows[0][5]) == "0.25000000"
    assert str(rows[0][6]) == "0.07000000"
    assert str(rows[0][7]) == "-0.01000000"
    assert rows[0][8:11] == (2, 1, 50000)
    assert rows[0][11:13] == (date(2026, 10, 1), date(2026, 10, 31))
    assert rows[0][13:15] == (_URL, _DOC_HASH)
    assert rows[0][15].startswith("LOCAL_IMPORT/etf_product_disclosures/")
