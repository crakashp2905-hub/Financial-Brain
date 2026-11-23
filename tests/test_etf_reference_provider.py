from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from financial_brain.config import Config
from financial_brain.ingest.reference import ETFReferenceJob
from financial_brain.providers.base import FetchError, FetchResult
from financial_brain.providers.etf_reference import NSEETFReferenceProvider, parse
from financial_brain.storage.db import Database

ETF_CSV = b"""Symbol,Underlying,Security Name,Date of Listing,Market Lot,ISIN Number,Face Value
NIFTYBEES,Nifty 50,Nippon India ETF Nifty 50,08-Jan-2002,1,INF000A01010,10
GOLDBEES,Gold,Nippon India ETF Gold BeES,15-Mar-2007,1,INF000A01028,1
"""


def test_nse_etf_reference_parser_keeps_identity_and_listing_fields() -> None:
    rows = parse(ETF_CSV, date(2026, 11, 23))
    assert len(rows) == 2
    assert rows[0] == {
        "observed_on": date(2026, 11, 23),
        "exchange": "NSE",
        "symbol": "NIFTYBEES",
        "underlying": "Nifty 50",
        "security_name": "Nippon India ETF Nifty 50",
        "listing_date": date(2002, 1, 8),
        "market_lot": 1,
        "isin": "INF000A01010",
        "face_value": Decimal("10"),
        "status": "LISTED",
    }


def test_nse_etf_provider_fetch_validates_before_returning(monkeypatch) -> None:
    provider = NSEETFReferenceProvider()
    monkeypatch.setattr(provider, "_http_get", lambda *a, **kw: (ETF_CSV, 200, "text/csv"))
    result = provider.fetch(date(2026, 11, 23))
    assert result.payload == ETF_CSV
    assert result.url.endswith("/eq_etfseclist.csv")
    assert result.filename.endswith("20261123.csv")


@pytest.mark.parametrize(
    "payload",
    [
        b"Symbol,Security Name\nX,Missing ISIN\n",
        b"Symbol,Underlying,Security Name,Date of Listing,Market Lot,ISIN Number\n"
        b"X,Index,ETF X,01-Jan-2027,1,INF000A01010\n",
        b"Symbol,Security Name,ISIN\nX,ETF X,INF000A01010\nX,ETF X,INF000A01028\n",
    ],
)
def test_nse_etf_reference_rejects_incomplete_or_future_records(payload: bytes) -> None:
    with pytest.raises(FetchError):
        parse(payload, date(2026, 11, 23))


class _FixtureETFProvider:
    source = "NSE"
    dataset = "etf_reference"

    def fetch(self, business_date: date) -> FetchResult:
        return FetchResult(
            payload=ETF_CSV,
            url="https://nsearchives.nseindia.com/content/equities/eq_etfseclist.csv",
            retrieved_at=datetime(2026, 11, 23, 10, tzinfo=timezone.utc),
            filename=f"etf-{business_date:%Y%m%d}.csv",
            content_type="text/csv",
            http_status=200,
        )


def test_etf_reference_job_lands_hashes_and_idempotently_persists_snapshot(tmp_path) -> None:
    cfg = Config(data_root=tmp_path).ensure()
    Database(cfg).migrate()
    job = ETFReferenceJob(cfg)
    day = date(2026, 11, 23)

    result = job.run(day, provider=_FixtureETFProvider())
    assert result.ok and result.rows_out == 2
    repeated = job.run(day, provider=_FixtureETFProvider())
    assert repeated.status == "skipped"

    with Database(cfg).connect() as con:
        rows = con.execute(
            """SELECT symbol, isin, status, source_sha256, record_sha256, lake_key
               FROM etf_reference_observations WHERE observed_on = ? ORDER BY symbol""",
            [day],
        ).fetchall()
    assert len(rows) == 2
    assert all(row[2] == "LISTED" and len(row[3]) == len(row[4]) == 64 for row in rows)
    assert all(row[5].startswith("NSE/etf_reference/") for row in rows)
