"""ATM quote archive records both executable and failed books."""
from __future__ import annotations

from datetime import datetime, timezone

import duckdb
import pytest

from financial_brain.ingest import options_quotes as OQ


def _contract():
    return {"instrument_token": 42, "tradingsymbol": "ABC26NOV100CE", "name": "ABC",
            "expiry": "2026-11-24", "strike": 100, "instrument_type": "CE", "lot_size": 50}


def test_store_keeps_contract_identity_and_full_book(tmp_path, monkeypatch):
    monkeypatch.setenv("FB_OPTIONS_ARCHIVE_LICENSE_APPROVED", "1")
    now = datetime(2026, 10, 22, 10, 0, tzinfo=OQ.IST)
    q = {"NFO:ABC26NOV100CE": {"last_price": 9.5, "timestamp": now, "oi": 1234, "volume": 567,
                                "depth": {"buy": [{"price": 9, "quantity": 200}],
                                          "sell": [{"price": 10, "quantity": 100}]}}}
    path = tmp_path / "options.duckdb"
    assert OQ.store([_contract()], q, {"NSE:ABC": {"last_price": 102, "timestamp": now}}, now,
                    path=path) == 1
    con = duckdb.connect(str(path), read_only=True)
    row = con.execute("SELECT instrument_token, strike, option_type, bid, ask, book_quality, "
                      "json_array_length(buy_depth), underlying_ltp, underlying_source_at, "
                      "open_interest, volume FROM option_quotes").fetchone()
    con.close()
    assert row[:8] == (42, 100.0, "CE", 9.0, 10.0, "TWO_SIDED", 1, 102.0)
    assert row[8].astimezone(OQ.IST) == now and row[9:] == (1234, 567)


def test_store_adds_new_observed_fields_to_an_existing_archive(tmp_path, monkeypatch):
    monkeypatch.setenv("FB_OPTIONS_ARCHIVE_LICENSE_APPROVED", "1")
    path = tmp_path / "legacy-options.duckdb"
    legacy_schema = OQ.SCHEMA.replace("    underlying_source_at TIMESTAMPTZ,\n", "")
    legacy_schema = legacy_schema.replace("    open_interest BIGINT,\n", "")
    legacy_schema = legacy_schema.replace("    volume BIGINT,\n", "")
    with duckdb.connect(str(path)) as con:
        con.execute(legacy_schema)
    now = datetime(2026, 10, 22, 10, 0, tzinfo=OQ.IST)
    q = {"NFO:ABC26NOV100CE": {"timestamp": now, "oi": 8, "volume": 9,
                                "depth": {"buy": [{"price": 9}], "sell": [{"price": 10}]}}}
    spots = {"NSE:ABC": {"last_price": 102, "timestamp": now}}
    assert OQ.store([_contract()], q, spots, now, path=path) == 1
    with duckdb.connect(str(path), read_only=True) as con:
        assert con.execute("SELECT open_interest, volume FROM option_quotes").fetchone() == (8, 9)


def test_incomplete_book_is_retained_not_counted_as_two_sided():
    now = datetime(2026, 10, 22, 10, 0, tzinfo=timezone.utc)
    q = {"NFO:ABC26NOV100CE": {"timestamp": now, "depth": {"buy": [{"price": 9}]}}}
    row = OQ.records([_contract()], q, {}, now)[0]
    assert row[-1] == "INCOMPLETE_OR_CROSSED"
    assert row[13] is None


def test_naive_receipt_time_is_rejected():
    with pytest.raises(ValueError, match="timezone"):
        OQ.records([], {}, {}, datetime(2026, 10, 22, 10))


def test_unlicensed_archive_does_not_create_database(tmp_path, monkeypatch):
    monkeypatch.delenv("FB_OPTIONS_ARCHIVE_LICENSE_APPROVED", raising=False)
    path = tmp_path / "forbidden.duckdb"
    assert OQ.store([_contract()], {}, {}, datetime(2026, 10, 22, 10, tzinfo=OQ.IST), path=path) == 0
    assert not path.exists()
