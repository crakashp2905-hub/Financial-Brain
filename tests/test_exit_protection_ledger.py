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


def _event(quantity="6", key="pos:1:stop", **changes):
    signal = ExitSignal(
            signal_id="550e8400-e29b-41d4-a716-446655440002", closes_position_id=POSITION_ID,
            strategy_id="breakout", strategy_version="1", instrument_id="NSE:INFY",
            closing_action=ClosingAction.SELL_TO_CLOSE, quote_source_id="kite:NSE",
            source_hash="c" * 64, occurred_at=datetime(2026, 11, 21, 11, tzinfo=timezone.utc),
            parent_signal_id=ENTRY_ID, reason="hard stop trigger observed",
        )
    values = {
        "exit_signal": signal, "protection_id": PROTECTION_ID, "trigger": ExitTrigger.HARD_STOP,
        "idempotency_key": key, "quantity": Decimal(quantity),
        "observed_trigger_at": datetime(2026, 11, 21, 10, 59, tzinfo=timezone.utc),
    }
    return EmittedExitEvent(**(values | changes))


def _armed(path):
    ledger = ExitProtectionLedger(DurableJournal(path), position_id=POSITION_ID)
    _prepare_entry(ledger)
    ledger.arm(_protection(), idempotency_key="protection:arm:1")
    ledger.record_entry_fill(entry_signal_id=ENTRY_ID, fill_id="entry-fill-1", quantity=Decimal("10"),
                             side=EntrySide.BUY, intent_id="entry-intent-1", idempotency_key="entry:fill:1")
    return ledger


def _prepare_entry(ledger):
    ledger.record_entry_signal(_entry(), snapshot={"setup": "breakout", "stop": "95"},
                               idempotency_key="entry:signal:1")
    ledger.record_entry_intent(entry_signal_id=ENTRY_ID, intent_id="entry-intent-1",
                               quantity=Decimal("10"), snapshot={"quantity": "10", "limit": "100"},
                               idempotency_key="entry:intent:1")


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
    _prepare_entry(ledger)
    with pytest.raises(ExitLedgerError, match="pre-armed hard-stop"):
        ledger.record_entry_fill(entry_signal_id=ENTRY_ID, fill_id="entry-fill-1", quantity=Decimal("10"),
                                 side=EntrySide.BUY, intent_id="entry-intent-1", idempotency_key="entry:fill:1")
    ledger.arm(_protection(), idempotency_key="protection:arm:1")
    ledger.record_entry_fill(entry_signal_id=ENTRY_ID, fill_id="entry-fill-1", quantity=Decimal("10"),
                             side=EntrySide.BUY, intent_id="entry-intent-1", idempotency_key="entry:fill:1")
    ledger.emit(_entry(), _protection(), _event("6", "pos:1:stop"))
    with pytest.raises(ExitLedgerError, match="does not match the recorded entry"):
        ledger.arm(_protection(parent_entry_signal_id="550e8400-e29b-41d4-a716-446655440009"),
                   idempotency_key="protection:wrong-parent")
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
        ledger.record_entry_fill(entry_signal_id=ENTRY_ID, intent_id="entry-intent-1",
                                 fill_id="entry-fill-2", quantity=Decimal("1"), side=EntrySide.SELL_SHORT,
                                 idempotency_key="entry:fill:2")


def test_oco_partial_fill_cancels_sibling_and_recovers_remaining_protection(tmp_path):
    path = tmp_path / "oco.journal"
    ledger = ExitProtectionLedger(DurableJournal(path), position_id=POSITION_ID)
    group = "550e8400-e29b-41d4-a716-446655440010"
    stop = _protection(oco_group_id=group)
    target = _protection(protection_id="550e8400-e29b-41d4-a716-446655440011",
                         trigger=ExitTrigger.TAKE_PROFIT, trigger_price=Decimal("110"), oco_group_id=group)
    ledger.arm(stop, idempotency_key="protection:stop")
    ledger.arm(target, idempotency_key="protection:target")
    _prepare_entry(ledger)
    ledger.record_entry_fill(entry_signal_id=ENTRY_ID, fill_id="entry-fill-1", quantity=Decimal("10"),
                             side=EntrySide.BUY, intent_id="entry-intent-1", idempotency_key="entry:fill:1")
    ledger.emit(_entry(), stop, _event("10", "exit:stop", oco_group_id=group))
    ledger.emit(_entry(), target, _event(
        "10", "exit:target", protection_id=target.protection_id, trigger=ExitTrigger.TAKE_PROFIT,
        oco_group_id=group,
    ))
    assert ledger.state.exit_pending_quantity == Decimal("10")

    ledger.record_exit_fill(exit_key="exit:stop", fill_id="stop-fill-partial", quantity=Decimal("2"),
                            idempotency_key="fill:stop:1")
    recovered = ExitProtectionLedger(DurableJournal(path), position_id=POSITION_ID)
    assert recovered.state.exit_filled_quantity == Decimal("2")
    assert recovered.state.exit_pending_quantity == Decimal("8")
    assert recovered.state.open_quantity == Decimal("8")
    assert not recovered.record_exit_fill(exit_key="exit:stop", fill_id="stop-fill-partial", quantity=Decimal("2"),
                                          idempotency_key="fill:stop:1")
    with pytest.raises(ExitLedgerError, match="suppressed after the first sibling fill"):
        recovered.emit(_entry(), target, _event(
            "8", "exit:target-after-fill", protection_id=target.protection_id,
            trigger=ExitTrigger.TAKE_PROFIT, oco_group_id=group,
        ))


def test_trigger_during_quote_outage_is_persisted_unresolved_until_exit_is_emitted(tmp_path):
    path = tmp_path / "unresolved.journal"
    ledger = _armed(path)
    kwargs = {
        "intent_id": "550e8400-e29b-41d4-a716-446655440012",
        "protection_id": PROTECTION_ID, "trigger": ExitTrigger.HARD_STOP,
        "quantity": Decimal("10"), "observed_at": datetime(2026, 11, 21, 11, tzinfo=timezone.utc),
        "reason": "quote_outage_after_stop_trigger", "idempotency_key": "unresolved:stop:1",
        "evidence_hash": "d" * 64,
    }
    assert ledger.record_unresolved_exit(**kwargs)
    assert not ledger.record_unresolved_exit(**kwargs)
    recovered = ExitProtectionLedger(DurableJournal(path), position_id=POSITION_ID)
    assert recovered.state.open_quantity == Decimal("10")
    assert recovered.state.unresolved_exit_intent_ids == {kwargs["intent_id"]}
    assert recovered.state.exit_filled_quantity == 0

    assert recovered.emit(_entry(), _protection(), _event(),
                          resolves_unresolved_intent_id=kwargs["intent_id"])
    final = ExitProtectionLedger(DurableJournal(path), position_id=POSITION_ID)
    assert final.state.unresolved_exit_intent_ids == frozenset()
    assert final.state.open_quantity == Decimal("10")


def test_unresolved_exit_requires_armed_protection_and_aware_timestamp(tmp_path):
    ledger = ExitProtectionLedger(DurableJournal(tmp_path / "unresolved.journal"), position_id=POSITION_ID)
    with pytest.raises(ExitLedgerError, match="durably armed"):
        ledger.record_unresolved_exit(
            intent_id="550e8400-e29b-41d4-a716-446655440012", protection_id=PROTECTION_ID,
            trigger=ExitTrigger.HARD_STOP, quantity=Decimal("1"), observed_at=NOW,
            reason="halt", idempotency_key="unresolved:1",
        )
    _prepare_entry(ledger)
    ledger.arm(_protection(), idempotency_key="protection:arm:1")
    ledger.record_entry_fill(entry_signal_id=ENTRY_ID, fill_id="entry-fill-1", quantity=Decimal("10"),
                             side=EntrySide.BUY, intent_id="entry-intent-1", idempotency_key="entry:fill:1")
    with pytest.raises(ExitLedgerError, match="timezone-aware"):
        ledger.record_unresolved_exit(
            intent_id="550e8400-e29b-41d4-a716-446655440012", protection_id=PROTECTION_ID,
            trigger=ExitTrigger.HARD_STOP, quantity=Decimal("1"),
            observed_at=datetime(2026, 11, 21, 11), reason="halt", idempotency_key="unresolved:1",
        )


def test_entry_signal_intent_snapshots_precede_fill_and_replay_with_hashes(tmp_path):
    path = tmp_path / "entry-lifecycle.journal"
    ledger = ExitProtectionLedger(DurableJournal(path), position_id=POSITION_ID)
    with pytest.raises(ExitLedgerError, match="ENTRY_SIGNAL"):
        ledger.record_entry_intent(entry_signal_id=ENTRY_ID, intent_id="entry-intent-1",
                                   quantity=Decimal("10"), snapshot={}, idempotency_key="entry:intent:early")
    _prepare_entry(ledger)
    ledger.arm(_protection(), idempotency_key="protection:arm:1")
    ledger.record_entry_fill(entry_signal_id=ENTRY_ID, intent_id="entry-intent-1", fill_id="fill-1",
                             quantity=Decimal("5"), side=EntrySide.BUY, idempotency_key="entry:fill:1")
    ledger.record_entry_fill(entry_signal_id=ENTRY_ID, intent_id="entry-intent-1", fill_id="fill-2",
                             quantity=Decimal("5"), side=EntrySide.BUY, idempotency_key="entry:fill:2")
    recovered = ExitProtectionLedger(DurableJournal(path), position_id=POSITION_ID)
    records = recovered.journal.records()
    event_types = [record["type"] for record in records if record.get("position_id") == POSITION_ID]
    assert event_types.index("ENTRY_SIGNAL") < event_types.index("ENTRY_INTENT") < event_types.index("ENTRY_FILL")
    assert all(len(record["snapshot_sha256"]) == 64 for record in records
               if record.get("type") in {"ENTRY_SIGNAL", "ENTRY_INTENT"})
    assert recovered.state.entry_filled_quantity == Decimal("10")


def test_position_close_requires_all_recorded_exit_fills_and_survives_restart(tmp_path):
    path = tmp_path / "closed.journal"
    ledger = _armed(path)
    ledger.emit(_entry(), _protection(), _event("10", "full-exit"))
    close = {"event_id": "550e8400-e29b-41d4-a716-446655440020", "reason": "fully filled exit",
             "occurred_at": datetime(2026, 11, 21, 12, tzinfo=timezone.utc),
             "idempotency_key": "position:closed:1"}
    with pytest.raises(ExitLedgerError, match="recorded fills"):
        ledger.record_position_closed(**close)
    ledger.record_exit_fill(exit_key="full-exit", fill_id="close-fill-1", quantity=Decimal("5"),
                            idempotency_key="exit:close:1")
    with pytest.raises(ExitLedgerError, match="recorded fills"):
        ledger.record_position_closed(**close)
    ledger.record_exit_fill(exit_key="full-exit", fill_id="close-fill-2", quantity=Decimal("5"),
                            idempotency_key="exit:close:2")
    assert ledger.record_position_closed(**close)
    recovered = ExitProtectionLedger(DurableJournal(path), position_id=POSITION_ID)
    assert recovered.state.open_quantity == 0
    assert recovered.state.closed_event_id == close["event_id"]
    assert not recovered.record_exit_fill(exit_key="full-exit", fill_id="close-fill-2", quantity=Decimal("5"),
                                          idempotency_key="exit:close:2")
    assert not recovered.record_position_closed(**close)
