from datetime import date
from decimal import Decimal

import pytest

from financial_brain.config import Config
from financial_brain.ingest.mf_portfolio_disclosure import ingest
from financial_brain.providers.base import FetchError
from financial_brain.providers.mf_portfolio_disclosure import parse
from financial_brain.storage.db import Database

HASH = "b" * 64
URL = "https://amc.example.in/portfolio/nov-2026.pdf"
CSV = ("scheme_code,portfolio_as_of,instrument_id,holding_name,asset_type,sector,weight_pct,"
       "market_value_inr,quantity,credit_rating,maturity_date,modified_duration_years,"
       "yield_to_maturity_pct,source_url,source_document_sha256,source_published_at\n"
       f"135762,2026-10-31,INE002A01018,Example Ltd,EQUITY,Financials,4.2,4200000,1000,,,,"
       f",{URL},{HASH},2026-11-15T10:00:00+05:30\n").encode()


def test_portfolio_parser_preserves_asof_source_and_nullable_metrics():
    row = parse(CSV, date(2026, 11, 23))[0]
    assert row["portfolio_as_of"] == date(2026, 10, 31)
    assert row["weight_pct"] == Decimal("4.2")
    assert row["credit_rating"] is None
    assert row["modified_duration_years"] is None
    assert row["source_document_sha256"] == HASH


@pytest.mark.parametrize("payload", [
    CSV.replace(b"2026-10-31", b"2026-12-31"),
    CSV.replace(HASH.encode(), b"not-a-hash"),
    CSV.replace(b",4.2,", b",140,") ,
    CSV.replace(URL.encode(), b"http://amc.example.in/file"),
])
def test_portfolio_parser_rejects_invalid_or_future_evidence(payload):
    with pytest.raises(FetchError):
        parse(payload, date(2026, 11, 23))


def test_portfolio_import_archives_and_is_idempotent(tmp_path):
    cfg = Config(data_root=tmp_path).ensure()
    result = ingest(CSV, date(2026, 11, 23), cfg)
    assert result == ingest(CSV, date(2026, 11, 23), cfg)
    with Database(cfg).connect() as con:
        rows = con.execute("""SELECT scheme_code, portfolio_as_of, instrument_id,
            weight_pct, credit_rating, source_document_sha256 FROM mf_portfolio_disclosures""").fetchall()
    assert len(rows) == 1
    assert rows[0] == ("135762", date(2026, 10, 31), "INE002A01018",
                       Decimal("4.20000000"), None, HASH)
