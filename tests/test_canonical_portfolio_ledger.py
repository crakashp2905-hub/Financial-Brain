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
        account_id=account_id, sleeve_id=sleeve_id, source_system=kwargs.pop("source_system", "fixture-source"),
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
    assert snapshot["pnl_promotion_gate"]["status"] == "BLOCKED"
    assert "UNRESOLVED_LEGACY_POSITIONS" in snapshot["pnl_promotion_gate"]["blockers"]
    assert "INCOMPLETE_POSITION_PROJECTION" in snapshot["pnl_promotion_gate"]["blockers"]
    assert "OPENING_BALANCE_MISSING" in snapshot["pnl_promotion_gate"]["blockers"]


def test_complete_broker_receipt_set_resolves_unresolved_exit_and_rechecks_gate(tmp_path):
    ledger = CanonicalPortfolioLedger(tmp_path / "portfolio.sqlite")
    ledger.import_events([
        event("opening-cash", EventKind.CASH_FLOW, cash_delta="10000",
              metadata={"cash_flow_type": "OPENING_BALANCE"}),
        event("trade-1:entry", EventKind.FILL, asset_class=AssetClass.EQUITY,
              instrument_id="NSE:INFY", side="BUY", quantity="2", price="100",
              occurred_at="2026-11-20T09:30:00+05:30",
              metadata={"legacy_trade_id": "trade-1"}),
        event("trade-1:unresolved-exit", EventKind.UNRESOLVED_POSITION,
              asset_class=AssetClass.EQUITY, instrument_id="NSE:INFY", quantity="2",
              occurred_at="2026-11-20T09:30:00+05:30",
              metadata={"legacy_trade_id": "trade-1", "trade_side": "long",
                        "reason": "recorded exit is missing"}),
    ])
    receipt = PortfolioEvent(
        account_id="paper-main", sleeve_id="intraday", source_system="BROKER_EXECUTION_RECEIPT",
        source_event_id="broker-trade-777", source_sha256=hashlib.sha256(b"broker receipt").hexdigest(),
        event_kind=EventKind.FILL, occurred_at="2026-11-20T10:00:00+05:30",
        asset_class=AssetClass.EQUITY, instrument_id="NSE:INFY", side="SELL", quantity="2", price="105",
        evidence_class="BROKER_EXECUTION_RECEIPT",
        metadata={"resolves_unresolved_event_id": "trade-1:unresolved-exit", "legacy_trade_id": "trade-1",
                  "broker_order_id": "order-777", "broker_trade_id": "broker-trade-777"},
    )

    result = ledger.append_execution_receipt_resolution(
        "paper-main", "trade-1:unresolved-exit", [receipt])
    assert result == {"received": 1, "added": 1, "already_present": 0}
    assert ledger.append_execution_receipt_resolution(
        "paper-main", "trade-1:unresolved-exit", [receipt]) == {
            "received": 1, "added": 0, "already_present": 1}
    snapshot = ledger.snapshot("paper-main")
    assert snapshot["unresolved_positions"] == []
    assert snapshot["positions"][0]["quantity"] == Decimal(0)
    assert snapshot["positions"][0]["realized_pnl"] == Decimal(10)
    assert snapshot["projection_complete"] is True
    assert snapshot["pnl_promotion_gate"]["status"] == "ELIGIBLE_FOR_REVIEW"


def test_local_paper_exit_cannot_resolve_unresolved_position(tmp_path):
    ledger = CanonicalPortfolioLedger(tmp_path / "portfolio.sqlite")
    unresolved = event("unresolved", EventKind.UNRESOLVED_POSITION, asset_class=AssetClass.EQUITY,
                       instrument_id="NSE:INFY", quantity="1",
                       metadata={"legacy_trade_id": "legacy-1", "trade_side": "long"})
    ledger.append(unresolved)
    paper_exit = event("paper-fill", EventKind.FILL, asset_class=AssetClass.EQUITY,
                       instrument_id="NSE:INFY", side="SELL", quantity="1", price="100",
                       metadata={"resolves_unresolved_event_id": "unresolved", "legacy_trade_id": "legacy-1",
                                 "broker_order_id": "not-a-broker-receipt", "broker_trade_id": "paper-fill"})
    with pytest.raises(CanonicalLedgerError, match="does not exactly resolve"):
        ledger.append_execution_receipt_resolution("paper-main", "unresolved", [paper_exit])
    assert ledger.snapshot("paper-main")["pnl_promotion_gate"]["status"] == "BLOCKED"


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


def test_canonical_ledger_rejects_unknown_or_conflicting_evidence_labels(tmp_path):
    ledger = CanonicalPortfolioLedger(tmp_path / "portfolio.sqlite")
    with pytest.raises(CanonicalLedgerError, match="recognized.*evidence class"):
        ledger.append(event("unsupported-label", EventKind.CASH_FLOW, cash_delta="1",
                            evidence_class="CONFIRMED"))
    with pytest.raises(CanonicalLedgerError, match="reserved for broker-sourced fill"):
        ledger.append(event("forged-receipt", EventKind.FILL, asset_class=AssetClass.EQUITY,
                            instrument_id="NSE:INFY", side="BUY", quantity="1", price="100",
                            evidence_class="BROKER_EXECUTION_RECEIPT"))
    with pytest.raises(CanonicalLedgerError, match="reserved for broker-sourced fill"):
        ledger.append(event("mislabelled-broker", EventKind.FILL, source_system="BROKER_EXECUTION_RECEIPT",
                            asset_class=AssetClass.EQUITY, instrument_id="NSE:INFY",
                            side="BUY", quantity="1", price="100", evidence_class="LOCAL_PAPER"))
