from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import duckdb

from financial_brain.etf_feed import collect_once, eligible_symbols
from financial_brain.forward_edge.quote_archive import QuoteArchive

IST = ZoneInfo("Asia/Kolkata")


def _full_quote(timestamp: str) -> dict:
    return {
        "timestamp": timestamp,
        "last_price": 105.0,
        "volume": 9000,
        "ohlc": {"open": 103, "high": 106, "low": 102, "close": 102},
        "depth": {
            "buy": [{"price": 104.9, "quantity": 250}],
            "sell": [{"price": 105.1, "quantity": 300}],
        },
    }


def test_eligible_etf_symbols_use_latest_reference_row_asof() -> None:
    con = duckdb.connect(":memory:")
    con.execute("""CREATE TABLE etf_reference_observations (
        observed_on DATE, exchange VARCHAR, symbol VARCHAR, isin VARCHAR, status VARCHAR)""")
    con.execute("""INSERT INTO etf_reference_observations VALUES
        ('2026-11-20','NSE','ETFOLD','INF000A01010','LISTED'),
        ('2026-11-23','NSE','ETFNEW','INF000A01010','LISTED'),
        ('2026-11-23','NSE','ETFGOLD','INF000A01028','LISTED'),
        ('2026-11-23','BSE','NOTNSE','INF000A01029','LISTED')""")
    try:
        assert eligible_symbols(con, date(2026, 11, 22)) == ["ETFOLD"]
        assert eligible_symbols(con, date(2026, 11, 23)) == ["ETFGOLD", "ETFNEW"]
    finally:
        con.close()


def test_collect_once_archives_full_quotes_with_event_and_receipt_times(tmp_path) -> None:
    calls: list[tuple[list[str], str]] = []
    # Keep the exchange session date deterministic; wall-clock dates make this
    # fixture fail as soon as UTC crosses into the next Asia/Kolkata session.
    receipt = datetime(2026, 11, 23, 10, 0, tzinfo=IST)

    def quote_fn(keys: list[str], kind: str) -> dict:
        calls.append((keys, kind))
        return {"NSE:ETFONE": _full_quote(receipt.strftime("%Y-%m-%dT%H:%M:%S"))}

    archive_path = tmp_path / "etf-events.sqlite"
    result = collect_once(["ETFONE", "ETFTWO"], quote_fn=quote_fn,
                          archive_path=archive_path, received_at=receipt)
    assert result == {"requested": 2, "archived": 1, "rejected": 0, "missing": 1}
    assert calls == [(["NSE:ETFONE", "NSE:ETFTWO"], "full")]
    archive = QuoteArchive(archive_path)
    try:
        events = archive.events_for_session(date(2026, 11, 23), source="KITE_CONNECT_READ_ONLY")
    finally:
        archive.close()
    assert len(events) == 1
    event = events[0]
    assert event.event_time == receipt
    assert event.received_at == receipt.astimezone(event.received_at.tzinfo)
    assert event.payload["bid"] == 104.9 and event.payload["ask"] == 105.1
    assert event.payload["volume"] == 9000
    assert len(event.payload["depth"]["buy"]) == 1
