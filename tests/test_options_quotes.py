"""ATM quote archive records both executable and failed books."""
from __future__ import annotations

from datetime import datetime, timezone

import duckdb
import pytest

from financial_brain.ingest import options_quotes as OQ


def _contract():
    return {"instrument_token": 42, "tradingsymbol": "ABC26NOV100CE", "name": "ABC",
            "expiry": "2026-11-24", "strike": 100, "instrument_type": "CE", "lot_size": 50,
            "tick_size": "0.05"}


def test_store_keeps_contract_identity_and_full_book(tmp_path, monkeypatch):
    monkeypatch.setenv("FB_OPTIONS_ARCHIVE_LICENSE_APPROVED", "1")
    now = datetime(2026, 10, 22, 10, 0, tzinfo=OQ.IST)
    q = {"NFO:ABC26NOV100CE": {"last_price": 9.5, "timestamp": now,
                                "depth": {"buy": [{"price": 9, "quantity": 200}],
                                          "sell": [{"price": 10, "quantity": 100}]}}}
    path = tmp_path / "options.duckdb"
    assert OQ.store([_contract()], q, {"NSE:ABC": {"last_price": 102}}, now, path=path) == 1
    con = duckdb.connect(str(path), read_only=True)
    row = con.execute("SELECT instrument_token, strike, option_type, bid, ask, book_quality, "
                      "json_array_length(buy_depth), underlying_ltp, available_at, spread_percent, "
                      "implied_vol_mid, implied_vol_price_basis, implied_vol_risk_free_rate, minimum_tick, "
                      "session_phase FROM option_quotes").fetchone()
    con.close()
    assert row[:8] == (42, 100.0, "CE", 9.0, 10.0, "TWO_SIDED", 1, 102.0)
    assert row[8] == now
    assert row[9] == pytest.approx(100 * (10 - 9) / 9.5)
    assert row[10] is not None and row[10] > 0
    assert row[11] == "OBSERVED_BID_ASK_MID_RESEARCH_ONLY"
    assert row[12] == pytest.approx(0.065) and row[13] == pytest.approx(0.05)
    assert row[14] == "UNKNOWN"


def test_incomplete_book_is_retained_not_counted_as_two_sided():
    now = datetime(2026, 10, 22, 10, 0, tzinfo=timezone.utc)
    q = {"NFO:ABC26NOV100CE": {"timestamp": now, "depth": {"buy": [{"price": 9}]}}}
    row = OQ.records([_contract()], q, {}, now)[0]
    assert row[-1] == "INCOMPLETE_OR_CROSSED"
    assert row[12] is None
    assert row[18] is None and row[19] is None and row[23] == "UNKNOWN"


def test_naive_receipt_time_is_rejected():
    with pytest.raises(ValueError, match="timezone"):
        OQ.records([], {}, {}, datetime(2026, 10, 22, 10))


def test_unlicensed_archive_does_not_create_database(tmp_path, monkeypatch):
    monkeypatch.delenv("FB_OPTIONS_ARCHIVE_LICENSE_APPROVED", raising=False)
    path = tmp_path / "forbidden.duckdb"
    assert OQ.store([_contract()], {}, {}, datetime(2026, 10, 22, 10, tzinfo=OQ.IST), path=path) == 0
    assert not path.exists()


def test_store_migrates_existing_option_archive_schema(tmp_path, monkeypatch):
    monkeypatch.setenv("FB_OPTIONS_ARCHIVE_LICENSE_APPROVED", "1")
    path = tmp_path / "old-options.duckdb"
    with duckdb.connect(str(path)) as con:
        con.execute("""CREATE TABLE option_quotes (
            received_at TIMESTAMPTZ NOT NULL, source_at TIMESTAMPTZ, instrument_token BIGINT NOT NULL,
            tradingsymbol VARCHAR NOT NULL, underlying VARCHAR NOT NULL, underlying_ltp DOUBLE,
            expiry DATE NOT NULL, strike DOUBLE NOT NULL, option_type VARCHAR NOT NULL, lot_size INTEGER NOT NULL,
            ltp DOUBLE, bid DOUBLE, ask DOUBLE, bid_qty BIGINT, ask_qty BIGINT, buy_depth JSON, sell_depth JSON,
            book_quality VARCHAR NOT NULL, PRIMARY KEY (received_at, instrument_token))""")
    now = datetime(2026, 10, 22, 10, 0, tzinfo=OQ.IST)
    q = {"NFO:ABC26NOV100CE": {"timestamp": now,
                                "depth": {"buy": [{"price": 9, "quantity": 200}],
                                          "sell": [{"price": 10, "quantity": 100}]}}}

    assert OQ.store([_contract()], q, {"NSE:ABC": {"last_price": 102}}, now, path=path) == 1

    with duckdb.connect(str(path), read_only=True) as con:
        row = con.execute("SELECT available_at, spread_percent, minimum_tick, session_phase "
                          "FROM option_quotes").fetchone()
    assert row == (now, pytest.approx(100 * (10 - 9) / 9.5), 0.05, "UNKNOWN")
