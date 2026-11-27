from decimal import Decimal
import hashlib
import random
from datetime import datetime, timedelta, timezone

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


def test_valuation_mark_cannot_carry_sell_semantics_or_realize_pnl(tmp_path):
    ledger = CanonicalPortfolioLedger(tmp_path / "valuation-only.sqlite")
    ledger.append(event("buy", EventKind.FILL, asset_class=AssetClass.EQUITY,
                        instrument_id="NSE:INFY", side="BUY", quantity="10", price="100"))
    with pytest.raises(CanonicalLedgerError, match="execution side/action"):
        ledger.append(event("forged-mark", EventKind.MARK, asset_class=AssetClass.EQUITY,
                            instrument_id="NSE:INFY", price="90", side="SELL"))
    ledger.append(event("last-good-mark", EventKind.MARK, asset_class=AssetClass.EQUITY,
                        instrument_id="NSE:INFY", price="90"))
    position = ledger.snapshot("paper-main")["positions"][0]
    assert position["quantity"] == Decimal("10")
    assert position["realized_pnl"] == Decimal("0")
    assert position["unrealized_pnl"] == Decimal("-100")


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
              instrument_id="NSE:INFY", side="SELL", position_action="SELL_SHORT", quantity="3", price="105",
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


def test_explicit_short_open_and_buy_to_cover_preserve_direction_and_realized_pnl(tmp_path):
    ledger = CanonicalPortfolioLedger(tmp_path / "portfolio.sqlite")
    ledger.import_events([
        event("opening-cash", EventKind.CASH_FLOW, cash_delta="10000"),
        event("short-entry", EventKind.FILL, asset_class=AssetClass.EQUITY, instrument_id="NSE:INFY",
              side="SELL", position_action="SELL_SHORT", quantity="10", price="100",
              occurred_at="2026-11-20T09:00:00+05:30"),
        event("cover-partial", EventKind.FILL, asset_class=AssetClass.EQUITY, instrument_id="NSE:INFY",
              side="BUY", position_action="BUY_TO_COVER", quantity="4", price="90",
              occurred_at="2026-11-20T10:00:00+05:30"),
        event("short-mark", EventKind.MARK, asset_class=AssetClass.EQUITY, instrument_id="NSE:INFY",
              price="90", occurred_at="2026-11-20T15:30:00+05:30"),
    ])

    snapshot = ledger.snapshot("paper-main")
    position, = snapshot["positions"]
    assert position["quantity"] == Decimal("-6")
    assert position["average_cost"] == Decimal("100")
    assert position["realized_pnl"] == Decimal("40")
    assert position["unrealized_pnl"] == Decimal("60")
    assert snapshot["cash"]["INR"] == Decimal("10640")
    actions = {row["source_event_id"]: row["position_action"] for row in ledger.events("paper-main")}
    assert actions["short-entry"] == "SELL_SHORT"
    assert actions["cover-partial"] == "BUY_TO_COVER"


def test_explicit_long_close_cannot_open_or_cross_into_a_short(tmp_path):
    ledger = CanonicalPortfolioLedger(tmp_path / "portfolio.sqlite")
    with pytest.raises(CanonicalLedgerError, match="SELL must close no more than an existing long"):
        ledger.append(event("short-with-sell", EventKind.FILL, asset_class=AssetClass.EQUITY,
                           instrument_id="NSE:INFY", side="SELL", position_action="SELL",
                           quantity="1", price="100"))
    assert ledger.event_count() == 0  # the invalid close never becomes durable


def test_short_actions_cannot_cross_existing_long_or_cover_more_than_short_inventory(tmp_path):
    crossed = CanonicalPortfolioLedger(tmp_path / "crossed.sqlite")
    with pytest.raises(CanonicalLedgerError, match="SELL_SHORT cannot increase or cross"):
        crossed.import_events([
            event("long-entry", EventKind.FILL, asset_class=AssetClass.EQUITY, instrument_id="NSE:INFY",
                  side="BUY", position_action="BUY", quantity="5", price="100",
                  occurred_at="2026-11-20T09:00:00+05:30"),
            event("crossing-short", EventKind.FILL, asset_class=AssetClass.EQUITY, instrument_id="NSE:INFY",
                  side="SELL", position_action="SELL_SHORT", quantity="6", price="100",
                  occurred_at="2026-11-20T09:01:00+05:30"),
        ])
    assert crossed.event_count() == 0  # batch rejection is atomic across the valid first row

    overcovered = CanonicalPortfolioLedger(tmp_path / "overcovered.sqlite")
    with pytest.raises(CanonicalLedgerError, match="BUY_TO_COVER must not exceed"):
        overcovered.import_events([
            event("short-entry", EventKind.FILL, asset_class=AssetClass.EQUITY, instrument_id="NSE:INFY",
                  side="SELL", position_action="SELL_SHORT", quantity="5", price="100",
                  occurred_at="2026-11-20T09:00:00+05:30"),
            event("excess-cover", EventKind.FILL, asset_class=AssetClass.EQUITY, instrument_id="NSE:INFY",
                  side="BUY", position_action="BUY_TO_COVER", quantity="6", price="90",
                  occurred_at="2026-11-20T09:01:00+05:30"),
        ])
    assert overcovered.event_count() == 0


def test_same_broker_trade_receipt_cannot_be_booked_under_two_source_systems(tmp_path):
    ledger = CanonicalPortfolioLedger(tmp_path / "portfolio.sqlite")
    receipt = PortfolioEvent(
        account_id="paper-main", sleeve_id="intraday", source_system="BROKER_EXECUTION_RECEIPT",
        source_event_id="broker-trade-777", source_sha256=HASH, event_kind=EventKind.FILL,
        occurred_at="2026-11-20T10:00:00+05:30", asset_class=AssetClass.EQUITY,
        instrument_id="NSE:INFY", side="BUY", quantity="1", price="100",
        evidence_class="BROKER_EXECUTION_RECEIPT",
        metadata={"broker_order_id": "order-777", "broker_trade_id": "broker-trade-777"},
    )
    assert ledger.append(receipt)
    duplicated_ingest = PortfolioEvent(
        account_id="paper-main", sleeve_id="intraday", source_system="SECOND_BROKER_IMPORT",
        source_event_id="broker-trade-777", source_sha256=HASH, event_kind=EventKind.FILL,
        occurred_at="2026-11-20T10:00:00+05:30", asset_class=AssetClass.EQUITY,
        instrument_id="NSE:INFY", side="BUY", quantity="1", price="100",
        evidence_class="BROKER_EXECUTION_RECEIPT",
        metadata={"broker_order_id": "order-777", "broker_trade_id": "broker-trade-777"},
    )

    with pytest.raises(CanonicalLedgerError, match="broker trade receipt ID is duplicated"):
        ledger.append(duplicated_ingest)
    assert ledger.event_count() == 1


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


def test_1000_seeded_cross_asset_event_sequences_preserve_ledger_invariants(tmp_path):
    """Exercise cash, identity, decimal, asset routing, time normalization and replay at scale."""
    rng = random.Random(20261124)
    ledger = CanonicalPortfolioLedger(tmp_path / "randomized-portfolio.sqlite")
    assets = (
        (AssetClass.EQUITY, "NSE:INFY", Decimal("1")),
        (AssetClass.ETF, "NSE:NIFTYBEES", Decimal("1")),
        (AssetClass.OPTION, "NFO:NIFTY-2026-12-31-26000-CE", Decimal("25")),
        (AssetClass.MUTUAL_FUND, "INF000000001", Decimal("1")),
    )
    events = []
    expected = []
    base = datetime(2026, 11, 2, 3, 30, tzinfo=timezone.utc)

    def at_utc_minute(minute: int, offset_minutes: int) -> str:
        local = (base + timedelta(minutes=minute)).astimezone(
            timezone(timedelta(minutes=offset_minutes)))
        return local.isoformat()

    for sequence in range(1000):
        account = f"random-paper-{sequence:04d}"
        asset_class, instrument_id, multiplier = assets[sequence % len(assets)]
        sleeve = ("swing", "intraday", "options", "long_term")[sequence % len(assets)]
        quantity_scale = 6 if asset_class is AssetClass.MUTUAL_FUND else 2
        scale = Decimal(10) ** quantity_scale
        buy_quantity = Decimal(rng.randint(100, 100_000)) / scale
        sell_quantity = buy_quantity * Decimal(rng.randint(1, 100)) / Decimal(100)
        buy_price = Decimal(rng.randint(100, 500_000)) / Decimal(100)
        sell_price = Decimal(rng.randint(100, 500_000)) / Decimal(100)
        mark_price = Decimal(rng.randint(100, 500_000)) / Decimal(100)
        fee = Decimal(rng.randint(0, 500)) / Decimal(100)
        offset = (0, 330, 345)[sequence % 3]
        prefix = f"seq-{sequence:04d}"
        events.extend((
            event(f"{prefix}-opening", EventKind.CASH_FLOW, account_id=account, sleeve_id=sleeve,
                  cash_delta="10000000", occurred_at=at_utc_minute(0, offset),
                  metadata={"cash_flow_type": "OPENING_BALANCE"}),
            event(f"{prefix}-buy", EventKind.FILL, account_id=account, sleeve_id=sleeve,
                  asset_class=asset_class, instrument_id=instrument_id, side="BUY",
                  quantity=str(buy_quantity), price=str(buy_price), multiplier=str(multiplier),
                  occurred_at=at_utc_minute(5, offset)),
            event(f"{prefix}-sell", EventKind.FILL, account_id=account, sleeve_id=sleeve,
                  asset_class=asset_class, instrument_id=instrument_id, side="SELL",
                  quantity=str(sell_quantity), price=str(sell_price), multiplier=str(multiplier),
                  occurred_at=at_utc_minute(30, offset)),
            event(f"{prefix}-fee", EventKind.FEE, account_id=account, sleeve_id=sleeve,
                  cash_delta=str(-fee), occurred_at=at_utc_minute(31, offset)),
            event(f"{prefix}-mark", EventKind.MARK, account_id=account, sleeve_id=sleeve,
                  asset_class=asset_class, instrument_id=instrument_id, price=str(mark_price),
                  occurred_at=at_utc_minute(360, offset)),
        ))
        expected.append((account, instrument_id, buy_quantity - sell_quantity, buy_quantity,
                         sell_quantity, buy_price, sell_price, mark_price, multiplier, fee))

    assert ledger.import_events(events) == {
        "received": 5000, "added": 5000, "already_present": 0,
    }
    assert ledger.import_events(events) == {
        "received": 5000, "added": 0, "already_present": 5000,
    }
    for (account, instrument_id, quantity, bought, sold, buy_price, sell_price,
         mark, multiplier, fee) in expected:
        snapshot = ledger.snapshot(account)
        assert snapshot["opening_balance_supplied"] is True
        assert snapshot["projection_complete"] is True
        assert snapshot["cash"]["INR"] == (
            Decimal("10000000") - bought * buy_price * multiplier
            + sold * sell_price * multiplier - fee
        )
        position = snapshot["positions"][0]
        assert position["instrument_id"] == instrument_id
        assert Decimal(position["quantity"]) == quantity
        assert Decimal(position["realized_pnl"]) == sold * (sell_price - buy_price) * multiplier
        assert Decimal(position["unrealized_pnl"]) == (mark - buy_price) * quantity * multiplier


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


def test_zero_value_expiry_is_option_only_and_closes_without_synthetic_cashflow(tmp_path):
    ledger = CanonicalPortfolioLedger(tmp_path / "portfolio.sqlite")
    ledger.import_events([
        event("option-open", EventKind.FILL, asset_class=AssetClass.OPTION,
              instrument_id="NIFTY-100-CE", side="BUY", quantity="50", price="10"),
        event("option-expiry", EventKind.EXPIRY_SETTLEMENT, asset_class=AssetClass.OPTION,
              instrument_id="NIFTY-100-CE", side="SELL", quantity="50", price="0"),
    ])
    state = ledger.snapshot("paper-main")
    option = next(row for row in state["positions"] if row["instrument_id"] == "NIFTY-100-CE")
    assert option["quantity"] == 0
    assert option["realized_pnl"] == Decimal("-500")
    assert state["cash"] == {"INR": Decimal("-500")}

    with pytest.raises(CanonicalLedgerError, match="expiry settlement events must close an option"):
        ledger.append(event("equity-expiry", EventKind.EXPIRY_SETTLEMENT,
                            asset_class=AssetClass.EQUITY, instrument_id="NSE:INFY",
                            side="SELL", quantity="1", price="0"))

