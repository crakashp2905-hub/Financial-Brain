from datetime import datetime, timezone
from decimal import Decimal

import pytest

from financial_brain.forward_edge.durable import DurableJournal
from financial_brain.kernel.exit_protection_ledger import ExitLedgerError, ExitProtectionLedger
from financial_brain.kernel.position_signals import (
    ClosingAction, EmittedExitEvent, EntrySide, EntrySignal, ExitProtectionIntent, ExitSignal,
)
from financial_brain.kernel.trader_signal import ExitTrigger


ENTRY_ID = "550e8400-e29b-41d4-a716-446655440000"
POSITION_ID = "550e8400-e29b-41d4-a716-446655440001"
PROTECTION_ID = "550e8400-e29b-41d4-a716-446655440003"
NOW = datetime(2026, 11, 21, 10, tzinfo=timezone.utc)


def _entry():
    return EntrySignal(
        signal_id=ENTRY_ID, opens_position_id=POSITION_ID, strategy_id="breakout",
        strategy_version="1", instrument_id="NSE:INFY", side=EntrySide.BUY,
        quote_source_id="kite:NSE", source_hash="a" * 64, occurred_at=NOW,
    )


def _protection(quantity="10", **changes):
    values = {
        "protection_id": PROTECTION_ID, "position_id": POSITION_ID,
        "parent_entry_signal_id": ENTRY_ID, "trigger": ExitTrigger.HARD_STOP,
        "trigger_price": Decimal("95"), "quantity": Decimal(quantity),
        "created_at": NOW, "source_hash": "b" * 64,
    }
    return ExitProtectionIntent(**(values | changes))


def _event(quantity="6", key="pos:1:stop"):
    return EmittedExitEvent(
        exit_signal=ExitSignal(
            signal_id="550e8400-e29b-41d4-a716-446655440002", closes_position_id=POSITION_ID,
            strategy_id="breakout", strategy_version="1", instrument_id="NSE:INFY",
            closing_action=ClosingAction.SELL_TO_CLOSE, quote_source_id="kite:NSE",
            source_hash="c" * 64, occurred_at=datetime(2026, 11, 21, 11, tzinfo=timezone.utc),
            parent_signal_id=ENTRY_ID, reason="hard stop trigger observed",
        ),
        protection_id=PROTECTION_ID, trigger=ExitTrigger.HARD_STOP,
        idempotency_key=key, quantity=Decimal(quantity),
        observed_trigger_at=datetime(2026, 11, 21, 10, 59, tzinfo=timezone.utc),
    )


def _armed(path):
    ledger = ExitProtectionLedger(DurableJournal(path), position_id=POSITION_ID)
    ledger.record_entry_fill(entry_signal_id=ENTRY_ID, fill_id="entry-fill-1", quantity=Decimal("10"),
                             side=EntrySide.BUY, idempotency_key="entry:fill:1")
    ledger.arm(_protection(), idempotency_key="protection:arm:1")
    return ledger


def test_trigger_and_partial_exit_fills_recover_from_hash_chained_journal(tmp_path):
    path = tmp_path / "position.journal"
    ledger = _armed(path)
    assert ledger.emit(_entry(), _protection(), _event())
    assert not ledger.emit(_entry(), _protection(), _event())
    ledger.record_exit_fill(exit_key="pos:1:stop", fill_id="exit-fill-1", quantity=Decimal("2"),
                            idempotency_key="exit:fill:1")

    recovered = ExitProtectionLedger(DurableJournal(path), position_id=POSITION_ID)
    assert recovered.state.entry_filled_quantity == Decimal("10")
    assert recovered.state.exit_filled_quantity == Decimal("2")
    assert recovered.state.exit_pending_quantity == Decimal("4")
    assert recovered.state.open_quantity == Decimal("8")
    assert recovered.state.emitted_idempotency_keys == {"pos:1:stop"}


def test_duplicate_fill_is_idempotent_but_conflicting_id_is_rejected(tmp_path):
    ledger = _armed(tmp_path / "position.journal")
    ledger.emit(_entry(), _protection(), _event())
    kwargs = {"exit_key": "pos:1:stop", "fill_id": "exit-fill-1", "quantity": Decimal("2"),
              "idempotency_key": "exit:fill:1"}
    assert ledger.record_exit_fill(**kwargs)
    assert not ledger.record_exit_fill(**kwargs)
    with pytest.raises(ExitLedgerError, match="fill id conflicts"):
        ledger.record_exit_fill(**(kwargs | {"quantity": Decimal("3")}))


def test_exit_must_be_armed_and_cannot_reserve_more_than_unreserved_open_quantity(tmp_path):
    path = tmp_path / "position.journal"
    ledger = ExitProtectionLedger(DurableJournal(path), position_id=POSITION_ID)
    ledger.record_entry_fill(entry_signal_id=ENTRY_ID, fill_id="entry-fill-1", quantity=Decimal("10"),
                             side=EntrySide.BUY, idempotency_key="entry:fill:1")
    with pytest.raises(ExitLedgerError, match="durably armed"):
        ledger.emit(_entry(), _protection(), _event())
    ledger.arm(_protection(), idempotency_key="protection:arm:1")
    with pytest.raises(ExitLedgerError, match="does not match the recorded entry"):
        ledger.arm(_protection(parent_entry_signal_id="550e8400-e29b-41d4-a716-446655440009"),
                   idempotency_key="protection:wrong-parent")
    ledger.emit(_entry(), _protection(), _event("6"))
    with pytest.raises(ExitLedgerError, match="unreserved open"):
        ledger.emit(_entry(), _protection(), _event("5", "pos:1:other"))
    assert ledger.state.exit_pending_quantity == Decimal("6")


def test_exit_fill_cannot_exceed_trigger_or_unknown_trigger(tmp_path):
    ledger = _armed(tmp_path / "position.journal")
    with pytest.raises(ExitLedgerError, match="unknown emitted exit"):
        ledger.record_exit_fill(exit_key="missing", fill_id="f1", quantity=Decimal("1"),
                                idempotency_key="fill:missing")
    ledger.emit(_entry(), _protection(), _event("3"))
    with pytest.raises(ExitLedgerError, match="exceeds emitted exit intent"):
        ledger.record_exit_fill(exit_key="pos:1:stop", fill_id="f1", quantity=Decimal("4"),
                                idempotency_key="fill:over")


def test_entry_fill_identity_and_side_cannot_change_mid_position(tmp_path):
    ledger = _armed(tmp_path / "position.journal")
    with pytest.raises(ExitLedgerError, match="one signal identity and side"):
        ledger.record_entry_fill(entry_signal_id="550e8400-e29b-41d4-a716-446655440009",
                                 fill_id="entry-fill-2", quantity=Decimal("1"), side=EntrySide.BUY,
                                 idempotency_key="entry:fill:2")
