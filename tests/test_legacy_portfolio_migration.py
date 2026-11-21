from decimal import Decimal

import duckdb

from financial_brain.portfolio.legacy_migration import intraday_row_events, swing_row_events
from financial_brain.portfolio.canonical_ledger import CanonicalPortfolioLedger, EventKind
from financial_brain.portfolio.legacy_migration import audit_legacy_migration, migrate_legacy_paper_books


def test_intraday_record_maps_source_persisted_entry_exit_and_costs():
    events = intraday_row_events({
        "trade_id": "trade-1", "mode": "live", "version": "v1", "day": "2026-11-20",
        "strategy": "ORB", "symbol": "INFY", "side": "long", "qty": 4,
        "entry_ts": "2026-11-20T09:31:00", "entry_px": 100,
        "exit_ts": "2026-11-20T15:15:00", "exit_px": 110, "costs": 3.5,
    })
    assert [item.event_kind for item in events] == [EventKind.FILL, EventKind.FILL, EventKind.FEE]
    assert events[0].side == "BUY" and events[1].side == "SELL"
    assert events[0].evidence_class == "LOCAL_PAPER"
    assert events[0].occurred_at == "2026-11-20T09:31:00+05:30"
    assert events[0].source_sha256 == events[1].source_sha256 == events[2].source_sha256


def test_intraday_open_position_is_retained_without_a_synthetic_exit():
    events = intraday_row_events({
        "trade_id": "open-short", "mode": "live", "version": "v1", "day": "2026-11-03",
        "strategy": "ORB", "symbol": "INFY", "side": "short", "qty": 2,
        "entry_ts": "2026-11-03T09:31:00", "entry_px": 100, "exit_ts": None, "exit_px": None,
    })
    assert [item.event_kind for item in events] == [EventKind.FILL, EventKind.UNRESOLVED_POSITION]
    assert events[0].side == "SELL"
    assert events[0].evidence_class == "LOCAL_PAPER"


def test_swing_records_preserve_exit_cost_and_last_close_mark():
    closed = swing_row_events({
        "version": "swing-v1", "mode": "replay", "signal_day": "2026-11-18", "ticker": "INFY",
        "type": "swing", "shares": 10, "entry_day": "2026-11-19", "entry_px": 100,
        "exit_day": "2026-11-20", "exit_px": 110, "cost": 0.01,
    })
    assert [item.event_kind for item in closed] == [EventKind.FILL, EventKind.FILL, EventKind.FEE]
    assert closed[0].evidence_class == "HISTORICAL_REPLAY"
    assert closed[0].occurred_at == "2026-11-19T15:30:00+05:30"
    assert closed[2].cash_delta == Decimal("-10.00")

    opened = swing_row_events({
        "version": "swing-v1", "mode": "live", "signal_day": "2026-11-18", "ticker": "INFY",
        "type": "swing", "shares": 10, "entry_day": "2026-11-19", "entry_px": 100,
        "exit_day": None, "exit_px": None, "last_day": "2026-11-20", "last_px": 105,
    })
    assert [item.event_kind for item in opened] == [EventKind.FILL, EventKind.MARK]
    assert opened[1].price == Decimal("105")


def test_migrated_fill_projection_uses_chronology_and_historical_source_mark(tmp_path):
    ledger = CanonicalPortfolioLedger(tmp_path / "portfolio.sqlite")
    events = swing_row_events({
        "version": "swing-v1", "mode": "live", "signal_day": "2026-11-18", "ticker": "INFY",
        "type": "swing", "shares": 10, "entry_day": "2026-11-19", "entry_px": 100,
        "exit_day": None, "exit_px": None, "last_day": "2026-11-20", "last_px": 105,
    })
    ledger.import_events(events)
    snapshot = ledger.snapshot("swing:swing-v1:live")
    position = snapshot["positions"][0]
    assert position["quantity"] == Decimal("10")
    assert position["mark"] == Decimal("105")
    assert position["unrealized_pnl"] == Decimal("50")
    assert snapshot["projection_complete"] is True
    assert snapshot["opening_balance_supplied"] is False


def test_source_book_migration_is_read_only_idempotent_and_reports_unavailable_desks(tmp_path):
    data_dir = tmp_path / "source"
    data_dir.mkdir()
    intraday_path = data_dir / "paper_live.duckdb"
    con = duckdb.connect(str(intraday_path))
    con.execute("""CREATE TABLE trades (
        trade_id VARCHAR, mode VARCHAR, version VARCHAR, day VARCHAR, strategy VARCHAR, symbol VARCHAR,
        side VARCHAR, qty INTEGER, entry_ts VARCHAR, entry_px DOUBLE, exit_ts VARCHAR, exit_px DOUBLE,
        costs DOUBLE)""")
    con.execute("""INSERT INTO trades VALUES ('id-1','live','v1','2026-11-20','ORB','INFY','long',2,
                '2026-11-20T09:31:00',100,'2026-11-20T15:15:00',105,2)""")
    con.close()
    swing_path = data_dir / "paper_book.duckdb"
    con = duckdb.connect(str(swing_path))
    con.execute("""CREATE TABLE positions (
        version VARCHAR, mode VARCHAR, signal_day DATE, ticker VARCHAR, type VARCHAR, shares INTEGER,
        entry_day DATE, entry_px DOUBLE, exit_day DATE, exit_px DOUBLE, last_day DATE, last_px DOUBLE, cost DOUBLE)""")
    con.execute("""INSERT INTO positions VALUES ('swing-v1','live',DATE '2026-11-18','TCS','swing',5,
                DATE '2026-11-19',100,NULL,NULL,DATE '2026-11-20',110,0.01)""")
    con.close()

    output = tmp_path / "canonical.sqlite"
    first = migrate_legacy_paper_books(data_dir, output)
    second = migrate_legacy_paper_books(data_dir, output)
    assert first["sources"]["intraday"]["events"]["added"] == 3
    assert first["sources"]["swing"]["events"]["added"] == 2
    assert second["sources"]["intraday"]["events"]["added"] == 0
    assert second["sources"]["swing"]["events"]["added"] == 0
    assert first["unavailable_desks"] == ["options", "ETF-specific", "mutual_fund"]
    assert first["original_databases_modified"] is False
    ledger = CanonicalPortfolioLedger(output)
    assert len(ledger.events("intraday:v1:live")) == 3
    assert len(ledger.events("swing:swing-v1:live")) == 2
    audit = audit_legacy_migration(data_dir, output, captured_at_utc="2026-11-21T00:00:00+00:00")
    assert audit["ledger"]["source_expected_events"] == 5
    assert audit["ledger"]["source_events_verified"] == 5
    assert audit["ledger"]["missing_source_events"] == 0
    assert audit["ledger"]["mismatched_source_events"] == 0
    assert audit["ledger"]["ledger_events_without_scanned_sources"] == 0
    assert audit["source_rows_contain_no_trade_level_values"] is True
