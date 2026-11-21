from decimal import Decimal
import hashlib

import pytest

from financial_brain.portfolio.canonical_ledger import (
    AssetClass,
    CanonicalLedgerError,
    CanonicalPortfolioLedger,
    EventKind,
    PortfolioEvent,
)


HASH = hashlib.sha256(b"source-row").hexdigest()


def event(event_id, kind, **kwargs):
    account_id = kwargs.pop("account_id", "paper-main")
    sleeve_id = kwargs.pop("sleeve_id", "intraday")
    return PortfolioEvent(
        account_id=account_id, sleeve_id=sleeve_id, source_system="fixture-source",
            source_event_id=event_id, source_sha256=kwargs.pop("source_sha256", HASH), event_kind=kind,
            occurred_at=kwargs.pop("occurred_at", "2026-11-20T10:00:00+05:30"), **kwargs,
    )


def test_cross_asset_fills_cash_marks_and_realized_unrealized_pnl_replay(tmp_path):
    ledger = CanonicalPortfolioLedger(tmp_path / "portfolio.sqlite")
    events = [
        event("cash", EventKind.CASH_FLOW, cash_delta="100000"),
        event("eq-buy", EventKind.FILL, asset_class=AssetClass.EQUITY, instrument_id="NSE:INFY",
              side="BUY", quantity="10", price="100", occurred_at="2026-11-20T09:00:00+05:30"),
        event("eq-sell", EventKind.FILL, asset_class=AssetClass.EQUITY, instrument_id="NSE:INFY",
              side="SELL", quantity="4", price="110", occurred_at="2026-11-20T09:30:00+05:30"),
        event("eq-mark", EventKind.MARK, asset_class=AssetClass.EQUITY, instrument_id="NSE:INFY",
              price="112", occurred_at="2026-11-20T15:30:00+05:30"),
        event("option-buy", EventKind.FILL, asset_class=AssetClass.OPTION,
              instrument_id="NFO:NIFTY-2026-11-26-26000-CE", side="BUY", quantity="50",
              price="100", multiplier="25", occurred_at="2026-11-20T10:00:00+05:30"),
        event("mf-buy", EventKind.FILL, asset_class=AssetClass.MUTUAL_FUND,
              instrument_id="INF000000001", side="BUY", quantity="12.5", price="80",
              occurred_at="2026-11-20T11:00:00+05:30"),
        event("etf-buy", EventKind.FILL, asset_class=AssetClass.ETF, instrument_id="NSE:NIFTYBEES",
              side="BUY", quantity="5", price="250", occurred_at="2026-11-20T12:00:00+05:30"),
        event("costs", EventKind.FEE, cash_delta="-25", occurred_at="2026-11-20T15:31:00+05:30"),
    ]
    assert ledger.import_events(events) == {"received": 8, "added": 8, "already_present": 0}
    assert ledger.import_events(events) == {"received": 8, "added": 0, "already_present": 8}

    snapshot = ledger.snapshot("paper-main")
    by_instrument = {row["instrument_id"]: row for row in snapshot["positions"]}
    assert snapshot["cash"]["INR"] == Decimal("100000") - Decimal("560") - Decimal("125000") \
        - Decimal("1000") - Decimal("1250") - Decimal("25")
    assert snapshot["opening_balance_supplied"] is False
    assert snapshot["cash_scope"] == "NET_EVENT_FLOWS_ONLY_NO_OPENING_BALANCE"
    assert by_instrument["NSE:INFY"]["quantity"] == Decimal("6")
    assert by_instrument["NSE:INFY"]["realized_pnl"] == Decimal("40")
    assert by_instrument["NSE:INFY"]["unrealized_pnl"] == Decimal("72")
    assert by_instrument["NFO:NIFTY-2026-11-26-26000-CE"]["quantity"] == Decimal("50")
    assert snapshot["projection_complete"] is False  # other open holdings still need explicit marks


def test_unresolved_position_is_visible_but_never_becomes_a_fill(tmp_path):
    ledger = CanonicalPortfolioLedger(tmp_path / "portfolio.sqlite")
    row = event("legacy-position", EventKind.UNRESOLVED_POSITION, asset_class=AssetClass.ETF,
                instrument_id="NSE:NIFTYBEES", quantity="10", occurred_at="2026-11-20T15:30:00+05:30",
                metadata={"reason": "legacy row has no execution receipt"})
    assert ledger.append(row)
    snapshot = ledger.snapshot("paper-main")
    assert snapshot["positions"] == []
    assert snapshot["cash"] == {}
    assert snapshot["unresolved_positions"][0]["metadata"]["reason"] == "legacy row has no execution receipt"
    assert snapshot["projection_complete"] is False


def test_sleeves_keep_separate_basis_and_pnl_before_consolidated_netting(tmp_path):
    ledger = CanonicalPortfolioLedger(tmp_path / "portfolio.sqlite")
    events = [
        event("desk-a-buy", EventKind.FILL, sleeve_id="swing", asset_class=AssetClass.EQUITY,
              instrument_id="NSE:INFY", side="BUY", quantity="5", price="100",
              occurred_at="2026-11-20T09:00:00+05:30"),
        event("desk-b-sell", EventKind.FILL, sleeve_id="intraday", asset_class=AssetClass.EQUITY,
              instrument_id="NSE:INFY", side="SELL", quantity="3", price="105",
              occurred_at="2026-11-20T09:30:00+05:30"),
        event("shared-mark", EventKind.MARK, sleeve_id="market-data", asset_class=AssetClass.EQUITY,
              instrument_id="NSE:INFY", price="110", occurred_at="2026-11-20T15:30:00+05:30"),
    ]
    ledger.import_events(events)
    snapshot = ledger.snapshot("paper-main")
    per_sleeve = {row["sleeve_id"]: row for row in snapshot["positions"]}
    assert per_sleeve["swing"]["average_cost"] == Decimal("100")
    assert per_sleeve["swing"]["unrealized_pnl"] == Decimal("50")
    assert per_sleeve["intraday"]["quantity"] == Decimal("-3")
    assert per_sleeve["intraday"]["unrealized_pnl"] == Decimal("-15")
    consolidated = snapshot["consolidated_positions"][0]
    assert consolidated["quantity"] == Decimal("2")
    assert consolidated["gross_abs_quantity"] == Decimal("8")
    assert consolidated["average_cost"] is None  # long/short basis must not be collapsed
    assert consolidated["unrealized_pnl"] == Decimal("35")


def test_as_of_cutoff_is_timezone_normalized_and_does_not_rewrite_marks(tmp_path):
    ledger = CanonicalPortfolioLedger(tmp_path / "portfolio.sqlite")
    ledger.append(event("buy", EventKind.FILL, asset_class=AssetClass.EQUITY, instrument_id="NSE:INFY",
                        side="BUY", quantity="2", price="100", occurred_at="2026-11-20T09:00:00+05:30"))
    ledger.append(event("mark", EventKind.MARK, asset_class=AssetClass.EQUITY, instrument_id="NSE:INFY",
                        price="105", occurred_at="2026-11-20T10:00:00+05:30"))
    prior = ledger.snapshot("paper-main", as_of="2026-11-20T03:45:00Z")
    after = ledger.snapshot("paper-main", as_of="2026-11-20T04:31:00Z")
    assert prior["positions"][0]["mark"] is None
    assert after["positions"][0]["mark"] == Decimal("105")
    assert after["positions"][0]["unrealized_pnl"] == Decimal("10")


def test_conflicting_replay_or_batch_is_rejected_atomically(tmp_path):
    ledger = CanonicalPortfolioLedger(tmp_path / "portfolio.sqlite")
    original = event("fill", EventKind.FILL, asset_class=AssetClass.EQUITY, instrument_id="NSE:INFY",
                     side="BUY", quantity="2", price="100")
    ledger.append(original)
    with pytest.raises(CanonicalLedgerError, match="different content"):
        ledger.append(event("fill", EventKind.FILL, asset_class=AssetClass.EQUITY, instrument_id="NSE:INFY",
                            side="BUY", quantity="3", price="100"))
    later = event("second", EventKind.CASH_FLOW, cash_delta="500")
    conflict = event("fill", EventKind.FILL, asset_class=AssetClass.EQUITY, instrument_id="NSE:INFY",
                     side="BUY", quantity="4", price="100")
    with pytest.raises(CanonicalLedgerError, match="different content"):
        ledger.import_events([later, conflict])
    assert [row["source_event_id"] for row in ledger.events("paper-main")] == ["fill"]


def test_events_refuse_missing_provenance_naive_time_and_bad_trade_fields(tmp_path):
    ledger = CanonicalPortfolioLedger(tmp_path / "portfolio.sqlite")
    with pytest.raises(CanonicalLedgerError, match="source_sha256"):
        ledger.append(event("no-hash", EventKind.CASH_FLOW, cash_delta="1", source_sha256=""))
    with pytest.raises(CanonicalLedgerError, match="timezone"):
        ledger.append(event("naive", EventKind.CASH_FLOW, cash_delta="1", occurred_at="2026-11-20T10:00:00"))
    with pytest.raises(CanonicalLedgerError, match="fill needs"):
        ledger.append(event("not-fill", EventKind.FILL, side="BUY", quantity="1", price="10"))

