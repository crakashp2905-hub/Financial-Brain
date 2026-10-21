"""ATM quote archive records both executable and failed books."""
from __future__ import annotations

from datetime import datetime, timezone

import duckdb
import pytest

from financial_brain.ingest import options_quotes as OQ


def _contract():
    return {"instrument_token": 42, "tradingsymbol": "ABC26NOV100CE", "name": "ABC",
            "expiry": "2026-11-24", "strike": 100, "instrument_type": "CE", "lot_size": 50}


def test_store_keeps_contract_identity_and_full_book(tmp_path):
    now = datetime(2026, 10, 22, 10, 0, tzinfo=OQ.IST)
    q = {"NFO:ABC26NOV100CE": {"last_price": 9.5, "timestamp": now,
                                "depth": {"buy": [{"price": 9, "quantity": 200}],
                                          "sell": [{"price": 10, "quantity": 100}]}}}
    path = tmp_path / "options.duckdb"
    assert OQ.store([_contract()], q, {"NSE:ABC": {"last_price": 102}}, now, path=path) == 1
    con = duckdb.connect(str(path), read_only=True)
    row = con.execute("SELECT instrument_token, strike, option_type, bid, ask, book_quality, "
                      "json_array_length(buy_depth), underlying_ltp FROM option_quotes").fetchone()
    con.close()
    assert row == (42, 100.0, "CE", 9.0, 10.0, "TWO_SIDED", 1, 102.0)


def test_incomplete_book_is_retained_not_counted_as_two_sided():
    now = datetime(2026, 10, 22, 10, 0, tzinfo=timezone.utc)
    q = {"NFO:ABC26NOV100CE": {"timestamp": now, "depth": {"buy": [{"price": 9}]}}}
    row = OQ.records([_contract()], q, {}, now)[0]
    assert row[-1] == "INCOMPLETE_OR_CROSSED"
    assert row[12] is None


def test_naive_receipt_time_is_rejected():
    with pytest.raises(ValueError, match="timezone"):
        OQ.records([], {}, {}, datetime(2026, 10, 22, 10))
