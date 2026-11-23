from decimal import Decimal
import hashlib

import duckdb

from financial_brain.portfolio.legacy_migration import (
    intraday_row_events, mf_paper_event_row_events, option_outcome_row_events, swing_row_events,
)
from financial_brain.portfolio.canonical_ledger import CanonicalPortfolioLedger, EventKind
from financial_brain.portfolio.legacy_migration import audit_legacy_migration, migrate_legacy_paper_books


def _options_row(payload: str, result: str, **extra):
    digest = _digest
    return {"cohort": "cohort-a", "rule_version": "rule-v1", "signal_id": "signal-1",
            "payload": payload, "payload_sha256": digest(payload), "intent_sha256": digest(payload),
            "result": result, "result_sha256": digest(result), **extra}


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


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


def test_options_outcomes_preserve_quote_fills_charges_and_missing_exit():
    payload = '{"cohort":"cohort-a","rule_version":"rule-v1","signal_id":"signal-1","token":123}'
    closed_result = ('{"status":"CLOSED","mode":"local_paper","contract":"NIFTY26NOV25000CE",'
                  '"token":123,"expiry":"2026-11-26","quantity":50,"entry_at":"2026-11-20T10:00:00+05:30",'
                  '"exit_at":"2026-11-20T10:15:00+05:30","entry_premium_inr":5000,'
                  '"exit_premium_inr":5500,"charges_inr":25}')
    closed = option_outcome_row_events(_options_row(payload, closed_result))
    assert [event.event_kind for event in closed] == [EventKind.FILL, EventKind.FILL, EventKind.FEE]
    assert [event.side for event in closed[:2]] == ["BUY", "SELL"]
    assert [event.price for event in closed[:2]] == [Decimal("100"), Decimal("110")]
    assert closed[0].asset_class.value == "OPTION"
    assert closed[-1].cash_delta == Decimal("-25")
    assert closed[0].evidence_class == "LOCAL_PAPER"

    unresolved_result = ('{"status":"NO_EXIT_QUOTE","mode":"local_paper","contract":"NIFTY26NOV25000CE",'
                  '"token":123,"expiry":"2026-11-26","quantity":50,"entry_at":"2026-11-20T10:00:00+05:30",'
                  '"entry_premium_inr":5000}')
    unresolved = option_outcome_row_events(
        _options_row(payload, unresolved_result, planned_exit_at="2026-11-20T10:15:00+05:30"))
    assert [event.event_kind for event in unresolved] == [EventKind.FILL, EventKind.UNRESOLVED_POSITION]
    assert unresolved[1].quantity == Decimal("50")


def test_mf_paper_events_preserve_nav_units_and_charges():
    common = {
        "account_id": "mf-account", "folio_id": "folio-1", "scheme_code": "12345",
        "plan": "DIRECT", "option": "GROWTH", "isin": "INF123A01011",
        "event_date": "2026-11-20", "event_sha256": "a" * 64,
    }
    subscription = mf_paper_event_row_events({
        **common, "event_id": "sub-1", "kind": "SUBSCRIBE", "nav_per_unit": "100",
        "units": "10", "gross_inr": "1005", "exit_load_inr": "0",
        "other_charges_inr": "5", "net_inr": "1000",
    })
    assert [event.event_kind for event in subscription] == [EventKind.FILL, EventKind.FEE]
    assert subscription[0].side == "BUY" and subscription[0].asset_class.value == "MUTUAL_FUND"
    assert subscription[0].quantity == Decimal("10") and subscription[0].price == Decimal("100")
    assert subscription[1].cash_delta == Decimal("-5")

    redemption = mf_paper_event_row_events({
        **common, "event_id": "red-1", "kind": "REDEEM", "nav_per_unit": "110",
        "units": "10", "gross_inr": "1100", "exit_load_inr": "10",
        "other_charges_inr": "5", "net_inr": "1085",
    })
    assert [event.event_kind for event in redemption] == [EventKind.FILL, EventKind.FEE]
    assert redemption[0].side == "SELL" and redemption[0].price == Decimal("110")
    assert redemption[1].cash_delta == Decimal("-15")


def test_migrate(tmp_path):
    data_dir = tmp_path / "s"
    data_dir.mkdir()
    path = data_dir / "options_paper_forward.duckdb"
    con = duckdb.connect(str(path))
    con.execute("""CREATE TABLE intents (
        cohort VARCHAR, rule_version VARCHAR, signal_id VARCHAR, planned_exit_at TIMESTAMPTZ,
        payload JSON, payload_sha256 VARCHAR)""")
    con.execute("""CREATE TABLE outcomes (
        cohort VARCHAR, rule_version VARCHAR, signal_id VARCHAR, evaluated_at TIMESTAMPTZ,
        intent_sha256 VARCHAR, result JSON, result_sha256 VARCHAR)""")
    payload = '{"cohort":"cohort-a","rule_version":"rule-v1","signal_id":"signal-1","token":123}'
    result = ('{"status":"CLOSED","mode":"local_paper","contract":"NIFTY26NOV25000CE",'
              '"token":123,"expiry":"2026-11-26","quantity":50,"entry_at":"2026-11-20T10:00:00+05:30",'
              '"exit_at":"2026-11-20T10:15:00+05:30","entry_premium_inr":5000,'
              '"exit_premium_inr":5500,"charges_inr":25}')
    con.execute("INSERT INTO intents VALUES (?, ?, ?, ?, ?, ?)", [
        "cohort-a", "rule-v1", "signal-1", "2026-11-20T10:15:00+05:30", payload, _digest(payload)])
    con.execute("INSERT INTO outcomes VALUES (?, ?, ?, ?, ?, ?, ?)", [
        "cohort-a", "rule-v1", "signal-1", "2026-11-20T10:16:00+05:30", _digest(payload),
        result, _digest(result)])
    con.close()

    mf_path = data_dir / "financial_brain.duckdb"
    con = duckdb.connect(str(mf_path))
    con.execute("""CREATE TABLE mf_paper_events (
        account_id VARCHAR, event_id VARCHAR, folio_id VARCHAR, kind VARCHAR, scheme_code VARCHAR,
        plan VARCHAR, option VARCHAR, isin VARCHAR, event_date DATE, nav_per_unit DECIMAL(38,18),
        units DECIMAL(38,18), gross_inr DECIMAL(38,18), exit_load_inr DECIMAL(38,18),
        other_charges_inr DECIMAL(38,18), net_inr DECIMAL(38,18), event_sha256 VARCHAR,
        created_at TIMESTAMPTZ)""")
    con.execute("""INSERT INTO mf_paper_events VALUES
        ('mf-account','sub-1','folio-1','SUBSCRIBE','12345','DIRECT','GROWTH','INF123A01011',
         DATE '2026-11-19',100,10,1005,0,5,1000,?,TIMESTAMPTZ '2026-11-19 10:00:00+05:30'),
        ('mf-account','red-1','folio-1','REDEEM','12345','DIRECT','GROWTH','INF123A01011',
         DATE '2026-11-20',110,10,1100,10,5,1085,?,TIMESTAMPTZ '2026-11-20 10:00:00+05:30')""",
        ["b" * 64, "c" * 64])
    con.close()

    output = tmp_path / "canonical.sqlite"
    first = migrate_legacy_paper_books(data_dir, output)
    second = migrate_legacy_paper_books(data_dir, output)
    assert first["sources"]["options"]["events"]["added"] == 3
    assert second["sources"]["options"]["events"]["added"] == 0
    assert first["unavailable_desks"] == ["ETF-specific"]
    audit = audit_legacy_migration(data_dir, output, captured_at_utc="2026-11-21T00:00:00+00:00")
    assert audit["sources"]["options"]["verification"] == {"verified": 3, "missing": 0, "mismatched": 0}
    ledger = CanonicalPortfolioLedger(output)
    snapshot = ledger.snapshot("options:cohort-a:rule-v1")
    assert snapshot["positions"][0]["quantity"] == 0
    assert snapshot["positions"][0]["realized_pnl"] == Decimal("500")
    assert audit["sources"]["mutual_fund"]["verification"] == {"verified": 4, "missing": 0, "mismatched": 0}
    mf_snapshot = ledger.snapshot("mutual_fund:mf-account")
    assert mf_snapshot["positions"][0]["quantity"] == 0
    assert mf_snapshot["positions"][0]["realized_pnl"] == Decimal("100")
    assert mf_snapshot["cash"]["INR"] == Decimal("80")


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
    assert first["pnl_promotion_gate"]["status"] == "BLOCKED"
    assert "OPENING_BALANCE_MISSING" in first["pnl_promotion_gate"]["reason_codes"]
    ledger = CanonicalPortfolioLedger(output)
    assert len(ledger.events("intraday:v1:live")) == 3
    assert len(ledger.events("swing:swing-v1:live")) == 2
    audit = audit_legacy_migration(data_dir, output, captured_at_utc="2026-11-21T00:00:00+00:00")
    assert audit["ledger"]["source_expected_events"] == 5
    assert audit["ledger"]["source_events_verified"] == 5
    assert audit["ledger"]["missing_source_events"] == 0
    assert audit["ledger"]["mismatched_source_events"] == 0
    assert audit["ledger"]["ledger_events_without_scanned_sources"] == 0
    assert audit["pnl_promotion_gate"]["status"] == "BLOCKED"
    assert audit["pnl_promotion_gate"]["blocked_accounts"] == 2
    assert "OPENING_BALANCE_MISSING" in audit["pnl_promotion_gate"]["reason_codes"]
    assert audit["source_rows_contain_no_trade_level_values"] is True
