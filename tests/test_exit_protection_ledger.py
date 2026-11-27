from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from financial_brain.forward_edge.durable import DurableJournal
from financial_brain.kernel.exit_protection_ledger import ExitLedgerError, ExitProtectionLedger
from financial_brain.kernel.exit_policy import ExitPolicy, ExitRule, ExitRuleAction
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
    ledger.arm(_protection(), exit_policy=_exit_policy(), idempotency_key="protection:arm:1")
    ledger.record_entry_fill(entry_signal_id=ENTRY_ID, fill_id="entry-fill-1", quantity=Decimal("10"),
                             side=EntrySide.BUY, intent_id="entry-intent-1", idempotency_key="entry:fill:1")
    return ledger


def _record_exit_fill(ledger, *, exit_key, fill_id, quantity, idempotency_key,
                      execution_id=None, simulated_at=None, occurred_at=None, price=Decimal("95")):
    execution_id = execution_id or f"execution:{fill_id}"
    simulated_at = simulated_at or datetime(2026, 11, 21, 11, tzinfo=timezone.utc)
    occurred_at = occurred_at or simulated_at + timedelta(milliseconds=1)
    ledger.record_exit_execution(
        exit_key=exit_key, execution_id=execution_id, simulated_at=simulated_at,
        quote_event_id=f"quote:{execution_id}",
        quote_observed_at=simulated_at - timedelta(milliseconds=2),
        quote_available_at=simulated_at - timedelta(milliseconds=1),
        quote_evidence_sha256="e" * 64, idempotency_key=f"{idempotency_key}:execution",
    )
    return ledger.record_exit_fill(
        exit_key=exit_key, execution_id=execution_id, fill_id=fill_id, quantity=quantity,
        occurred_at=occurred_at, price=price, idempotency_key=idempotency_key,
    )


def _prepare_entry(ledger):
    ledger.record_entry_signal(_entry(), snapshot={"setup": "breakout", "stop": "95"},
                               idempotency_key="entry:signal:1")
    ledger.record_entry_intent(entry_signal_id=ENTRY_ID, intent_id="entry-intent-1",
                               quantity=Decimal("10"), snapshot={"quantity": "10", "limit": "100"},
                               idempotency_key="entry:intent:1")


def _exit_policy():
    return ExitPolicy(
        strategy_id="breakout", strategy_version="1", policy_version="protective-exits-v1",
        rules=(
            ExitRule("hard-stop", ExitTrigger.HARD_STOP, ExitRuleAction.FULL_EXIT,
                     quantity_fraction=Decimal("1"), trigger_price=Decimal("95")),
            ExitRule("take-profit", ExitTrigger.TAKE_PROFIT, ExitRuleAction.FULL_EXIT,
                     quantity_fraction=Decimal("1"), trigger_price=Decimal("110")),
        ),
    )


def test_trigger_and_partial_exit_fills_recover_from_hash_chained_journal(tmp_path):
    path = tmp_path / "position.journal"
    ledger = _armed(path)
    assert ledger.emit(_entry(), _protection(), _event())
    assert not ledger.emit(_entry(), _protection(), _event())
    _record_exit_fill(ledger, exit_key="pos:1:stop", fill_id="exit-fill-1", quantity=Decimal("2"),
                      idempotency_key="exit:fill:1")

    recovered = ExitProtectionLedger(DurableJournal(path), position_id=POSITION_ID)
    assert recovered.state.entry_filled_quantity == Decimal("10")
    assert recovered.state.exit_filled_quantity == Decimal("2")
    assert recovered.state.exit_pending_quantity == Decimal("4")
    assert recovered.state.open_quantity == Decimal("8")
    assert recovered.state.emitted_idempotency_keys == {"pos:1:stop"}


def test_trigger_execution_observed_fill_and_daily_close_keep_distinct_clocks(tmp_path):
    path = tmp_path / "timed-exit.journal"
    ledger = _armed(path)
    ledger.emit(_entry(), _protection(), _event())
    trigger_at = _event().observed_trigger_at
    execution_at = datetime(2026, 11, 21, 11, 1, tzinfo=timezone.utc)
    quote_observed_at = execution_at - timedelta(seconds=2)
    quote_available_at = execution_at - timedelta(seconds=1)
    assert ledger.record_exit_execution(
        exit_key="pos:1:stop", execution_id="stop-exec-1", simulated_at=execution_at,
        quote_event_id="quote-1", quote_observed_at=quote_observed_at,
        quote_available_at=quote_available_at, quote_evidence_sha256="f" * 64,
        idempotency_key="stop-exec-1:journal",
    )
    fill_at = execution_at + timedelta(milliseconds=250)
    assert ledger.record_exit_fill(
        exit_key="pos:1:stop", execution_id="stop-exec-1", fill_id="stop-fill-1",
        quantity=Decimal("2"), occurred_at=fill_at, price=Decimal("94.95"),
        idempotency_key="stop-fill-1:journal",
    )
    recovered = ExitProtectionLedger(DurableJournal(path), position_id=POSITION_ID)
    records = recovered.journal.records()
    position_events = [row for row in records if row.get("position_id") == POSITION_ID]
    trigger = next(row for row in position_events if row["type"] == "EXIT_TRIGGERED")
    execution = next(row for row in position_events if row["type"] == "EXIT_EXECUTION")
    fill = next(row for row in position_events if row["type"] == "EXIT_FILL")
    assert datetime.fromisoformat(trigger["observed_trigger_at"]) == trigger_at
    assert datetime.fromisoformat(execution["simulated_at"]) == execution_at
    assert datetime.fromisoformat(fill["occurred_at"]) == fill_at
    assert fill["price"] == "94.95"


def test_execution_must_follow_trigger_and_use_an_available_observed_quote(tmp_path):
    ledger = _armed(tmp_path / "execution-order.journal")
    ledger.emit(_entry(), _protection(), _event())
    execution_at = datetime(2026, 11, 21, 11, tzinfo=timezone.utc)
    common = {"exit_key": "pos:1:stop", "execution_id": "exec-1",
              "quote_event_id": "quote-1", "quote_evidence_sha256": "e" * 64,
              "idempotency_key": "exec-1:journal"}
    with pytest.raises(ExitLedgerError, match="precede the observed exit trigger"):
        ledger.record_exit_execution(
            **(common | {"simulated_at": datetime(2026, 11, 21, 10, 58, tzinfo=timezone.utc),
                         "quote_observed_at": execution_at - timedelta(seconds=3),
                         "quote_available_at": execution_at - timedelta(seconds=2)})
        )
    with pytest.raises(ExitLedgerError, match="available by execution time"):
        ledger.record_exit_execution(
            **(common | {"simulated_at": execution_at,
                         "quote_observed_at": execution_at - timedelta(seconds=1),
                         "quote_available_at": execution_at + timedelta(milliseconds=1)})
        )


def test_fill_requires_persisted_execution_and_cannot_predate_it(tmp_path):
    ledger = _armed(tmp_path / "fill-clock.journal")
    ledger.emit(_entry(), _protection(), _event())
    execution_at = datetime(2026, 11, 21, 11, tzinfo=timezone.utc)
    ledger.record_exit_execution(
        exit_key="pos:1:stop", execution_id="exec-1", simulated_at=execution_at,
        quote_event_id="quote-1", quote_observed_at=execution_at - timedelta(seconds=2),
        quote_available_at=execution_at - timedelta(seconds=1), quote_evidence_sha256="e" * 64,
        idempotency_key="exec-1:journal",
    )
    with pytest.raises(ExitLedgerError, match="cannot precede paper execution"):
        ledger.record_exit_fill(
            exit_key="pos:1:stop", execution_id="exec-1", fill_id="fill-1", quantity=Decimal("1"),
            occurred_at=execution_at - timedelta(milliseconds=1), price=Decimal("95"),
            idempotency_key="fill-1:journal",
        )


def test_duplicate_fill_is_idempotent_but_conflicting_id_is_rejected(tmp_path):
    ledger = _armed(tmp_path / "position.journal")
    ledger.emit(_entry(), _protection(), _event())
    kwargs = {"exit_key": "pos:1:stop", "fill_id": "exit-fill-1", "quantity": Decimal("2"),
              "idempotency_key": "exit:fill:1"}
    assert _record_exit_fill(ledger, **kwargs)
    assert not _record_exit_fill(ledger, **kwargs)
    with pytest.raises(ExitLedgerError, match="fill id conflicts"):
        _record_exit_fill(ledger, **(kwargs | {"quantity": Decimal("3")}))


def test_exit_must_be_armed_and_cannot_reserve_more_than_unreserved_open_quantity(tmp_path):
    path = tmp_path / "position.journal"
    ledger = ExitProtectionLedger(DurableJournal(path), position_id=POSITION_ID)
    _prepare_entry(ledger)
    with pytest.raises(ExitLedgerError, match="pre-armed hard-stop"):
        ledger.record_entry_fill(entry_signal_id=ENTRY_ID, fill_id="entry-fill-1", quantity=Decimal("10"),
                                 side=EntrySide.BUY, intent_id="entry-intent-1", idempotency_key="entry:fill:1")
    ledger.arm(_protection(), exit_policy=_exit_policy(), idempotency_key="protection:arm:1")
    ledger.record_entry_fill(entry_signal_id=ENTRY_ID, fill_id="entry-fill-1", quantity=Decimal("10"),
                             side=EntrySide.BUY, intent_id="entry-intent-1", idempotency_key="entry:fill:1")
    ledger.emit(_entry(), _protection(), _event("6", "pos:1:stop"))
    with pytest.raises(ExitLedgerError, match="journaled entry signal"):
        ledger.arm(_protection(parent_entry_signal_id="550e8400-e29b-41d4-a716-446655440009"),
                   exit_policy=_exit_policy(),
                   idempotency_key="protection:wrong-parent")
    with pytest.raises(ExitLedgerError, match="unreserved open"):
        ledger.emit(_entry(), _protection(), _event("5", "pos:1:other"))
    assert ledger.state.exit_pending_quantity == Decimal("6")


def test_exit_fill_cannot_exceed_trigger_or_unknown_trigger(tmp_path):
    ledger = _armed(tmp_path / "position.journal")
    with pytest.raises(ExitLedgerError, match="unknown emitted exit"):
        _record_exit_fill(ledger, exit_key="missing", fill_id="f1", quantity=Decimal("1"),
                          idempotency_key="fill:missing")
    ledger.emit(_entry(), _protection(), _event("3"))
    with pytest.raises(ExitLedgerError, match="exceeds emitted exit intent"):
        _record_exit_fill(ledger, exit_key="pos:1:stop", fill_id="f1", quantity=Decimal("4"),
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
    _prepare_entry(ledger)
    ledger.arm(stop, exit_policy=_exit_policy(), idempotency_key="protection:stop")
    ledger.arm(target, exit_policy=_exit_policy(), idempotency_key="protection:target")
    ledger.record_entry_fill(entry_signal_id=ENTRY_ID, fill_id="entry-fill-1", quantity=Decimal("10"),
                             side=EntrySide.BUY, intent_id="entry-intent-1", idempotency_key="entry:fill:1")
    ledger.emit(_entry(), stop, _event("10", "exit:stop", oco_group_id=group))
    ledger.emit(_entry(), target, _event(
        "10", "exit:target", protection_id=target.protection_id, trigger=ExitTrigger.TAKE_PROFIT,
        oco_group_id=group,
    ))
    assert ledger.state.exit_pending_quantity == Decimal("10")

    _record_exit_fill(ledger, exit_key="exit:stop", fill_id="stop-fill-partial", quantity=Decimal("2"),
                      idempotency_key="fill:stop:1")
    recovered = ExitProtectionLedger(DurableJournal(path), position_id=POSITION_ID)
    assert recovered.state.exit_filled_quantity == Decimal("2")
    assert recovered.state.exit_pending_quantity == Decimal("8")
    assert recovered.state.open_quantity == Decimal("8")
    assert not _record_exit_fill(recovered, exit_key="exit:stop", fill_id="stop-fill-partial",
                                 quantity=Decimal("2"), idempotency_key="fill:stop:1")
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
    ledger.arm(_protection(), exit_policy=_exit_policy(), idempotency_key="protection:arm:1")
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
    ledger.arm(_protection(), exit_policy=_exit_policy(), idempotency_key="protection:arm:1")
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
    position_records = [record for record in records if record.get("position_id") == POSITION_ID]
    decision_hashes = {record.get("decision_sha256") for record in position_records}
    assert len(decision_hashes) == 1
    assert len(next(iter(decision_hashes))) == 64
    assert all(record.get("decision_sha256") for record in position_records)
    assert all(record["exit_policy_hash"] == _exit_policy().policy_hash for record in records
               if record.get("type") == "PROTECTION_ARMED")
    assert recovered.state.entry_filled_quantity == Decimal("10")


def _entry_intent_ledger(path, *, intent_snapshot=None):
    ledger = ExitProtectionLedger(DurableJournal(path), position_id=POSITION_ID)
    ledger.record_entry_signal(_entry(), snapshot={"setup": "breakout"}, idempotency_key="entry:signal")
    ledger.record_entry_intent(entry_signal_id=ENTRY_ID, intent_id="entry-intent-1",
                               quantity=Decimal("10"),
                               snapshot=intent_snapshot or {"quantity": "10", "limit": "100"},
                               idempotency_key="entry:intent")
    ledger.arm(_protection(), exit_policy=_exit_policy(), idempotency_key="protection:arm")
    return ledger


def test_entry_amendment_is_versioned_linked_and_sets_the_fill_cap(tmp_path):
    ledger = _entry_intent_ledger(tmp_path / "entry-amendment.journal")
    amended_snapshot = {"quantity": "8", "limit": "101", "reason": "price-band update"}
    assert ledger.amend_entry_intent(intent_id="entry-intent-1", quantity=Decimal("8"),
                                     snapshot=amended_snapshot, occurred_at=NOW + timedelta(seconds=1),
                                     source_hash="d" * 64, idempotency_key="entry:amend:1") is True
    assert ledger.amend_entry_intent(intent_id="entry-intent-1", quantity=Decimal("8"),
                                     snapshot=amended_snapshot, occurred_at=NOW + timedelta(seconds=1),
                                     source_hash="d" * 64, idempotency_key="entry:amend:1") is False
    assert ledger.record_entry_fill(entry_signal_id=ENTRY_ID, intent_id="entry-intent-1", fill_id="fill-8",
                                    quantity=Decimal("8"), side=EntrySide.BUY,
                                    idempotency_key="entry:fill:8") is True
    with pytest.raises(ExitLedgerError, match="fully filled entry intent"):
        ledger.amend_entry_intent(intent_id="entry-intent-1", quantity=Decimal("9"),
                                  snapshot={"quantity": "9"}, occurred_at=NOW + timedelta(seconds=2),
                                  source_hash="e" * 64, idempotency_key="entry:amend:2")
    records = [row for row in ledger.journal.records() if row.get("position_id") == POSITION_ID]
    amendment, = [row for row in records if row["type"] == "ENTRY_INTENT_AMENDED"]
    assert amendment["revision"] == 1
    assert amendment["decision_sha256"] == records[0]["decision_sha256"]
    assert amendment["snapshot_sha256"]
    assert records[1]["quantity"] == "10"  # original is retained unchanged


def test_entry_amendment_cannot_reduce_below_fills_or_rewrite_fill_capacity(tmp_path):
    ledger = _entry_intent_ledger(tmp_path / "entry-amend-cap.journal")
    ledger.record_entry_fill(entry_signal_id=ENTRY_ID, intent_id="entry-intent-1", fill_id="fill-5",
                             quantity=Decimal("5"), side=EntrySide.BUY, idempotency_key="entry:fill:5")
    with pytest.raises(ExitLedgerError, match="below already filled"):
        ledger.amend_entry_intent(intent_id="entry-intent-1", quantity=Decimal("4"),
                                  snapshot={"quantity": "4"}, occurred_at=NOW + timedelta(seconds=1),
                                  source_hash="f" * 64, idempotency_key="entry:amend:too-small")


def test_entry_cancel_is_idempotent_terminal_and_decision_linked(tmp_path):
    ledger = _entry_intent_ledger(tmp_path / "entry-cancel.journal")
    ledger.record_entry_fill(entry_signal_id=ENTRY_ID, intent_id="entry-intent-1", fill_id="fill-3",
                             quantity=Decimal("3"), side=EntrySide.BUY, idempotency_key="entry:fill:3")
    cancel_args = {"intent_id": "entry-intent-1", "occurred_at": NOW + timedelta(seconds=2),
                   "source_hash": "f" * 64, "reason": "operator confirmed cancel",
                   "idempotency_key": "entry:cancel"}
    assert ledger.cancel_entry_intent(**cancel_args) is True
    assert ledger.cancel_entry_intent(**cancel_args) is False
    with pytest.raises(ExitLedgerError, match="terminal entry intent"):
        ledger.record_entry_fill(entry_signal_id=ENTRY_ID, intent_id="entry-intent-1", fill_id="late-fill",
                                 quantity=Decimal("1"), side=EntrySide.BUY,
                                 idempotency_key="entry:fill:late")
    cancelled, = [row for row in ledger.journal.records() if row.get("type") == "ENTRY_INTENT_CANCELLED"]
    assert cancelled["remaining_quantity"] == "7"
    assert cancelled["decision_sha256"] == ledger.journal.records()[0]["decision_sha256"]


def test_entry_expiry_requires_frozen_deadline_and_blocks_later_fills(tmp_path):
    expires_at = NOW + timedelta(minutes=1)
    ledger = _entry_intent_ledger(tmp_path / "entry-expire.journal",
                                  intent_snapshot={"quantity": "10", "expires_at": expires_at.isoformat()})
    args = {"intent_id": "entry-intent-1", "source_hash": "e" * 64,
            "reason": "signal lifetime elapsed", "idempotency_key": "entry:expire"}
    with pytest.raises(ExitLedgerError, match="before its frozen deadline"):
        ledger.expire_entry_intent(occurred_at=NOW, **args)
    assert ledger.expire_entry_intent(occurred_at=expires_at + timedelta(seconds=1), **args) is True
    with pytest.raises(ExitLedgerError, match="terminal entry intent"):
        ledger.record_entry_fill(entry_signal_id=ENTRY_ID, intent_id="entry-intent-1", fill_id="late-fill",
                                 quantity=Decimal("1"), side=EntrySide.BUY,
                                 idempotency_key="entry:fill:after-expiry")


def test_armed_protection_must_match_strategy_version_and_policy_rule(tmp_path):
    ledger = ExitProtectionLedger(DurableJournal(tmp_path / "policy.journal"), position_id=POSITION_ID)
    _prepare_entry(ledger)
    wrong_strategy = ExitPolicy(
        strategy_id="other", strategy_version="1", policy_version="v1",
        rules=_exit_policy().rules,
    )
    with pytest.raises(ExitLedgerError, match="strategy identity"):
        ledger.arm(_protection(), exit_policy=wrong_strategy, idempotency_key="bad:strategy")
    wrong_level = ExitPolicy(
        strategy_id="breakout", strategy_version="1", policy_version="v2",
        rules=(ExitRule("hard-stop", ExitTrigger.HARD_STOP, ExitRuleAction.FULL_EXIT,
                        quantity_fraction=Decimal("1"), trigger_price=Decimal("94")),),
    )
    with pytest.raises(ExitLedgerError, match="not covered"):
        ledger.arm(_protection(), exit_policy=wrong_level, idempotency_key="bad:level")


def test_position_close_requires_all_recorded_exit_fills_and_survives_restart(tmp_path):
    path = tmp_path / "closed.journal"
    ledger = _armed(path)
    ledger.emit(_entry(), _protection(), _event("10", "full-exit"))
    close = {"event_id": "550e8400-e29b-41d4-a716-446655440020", "reason": "fully filled exit",
             "occurred_at": datetime(2026, 11, 21, 12, tzinfo=timezone.utc),
             "idempotency_key": "position:closed:1"}
    with pytest.raises(ExitLedgerError, match="recorded fills"):
        ledger.record_position_closed(**close)
    _record_exit_fill(ledger, exit_key="full-exit", fill_id="close-fill-1", quantity=Decimal("5"),
                      idempotency_key="exit:close:1")
    with pytest.raises(ExitLedgerError, match="recorded fills"):
        ledger.record_position_closed(**close)
    _record_exit_fill(ledger, exit_key="full-exit", fill_id="close-fill-2", quantity=Decimal("5"),
                      idempotency_key="exit:close:2")
    with pytest.raises(ExitLedgerError, match="cannot precede its latest observed exit fill"):
        ledger.record_position_closed(**(close | {
            "occurred_at": datetime(2026, 11, 21, 10, 59, tzinfo=timezone.utc),
            "event_id": "550e8400-e29b-41d4-a716-446655440021",
            "idempotency_key": "position:closed:too-early",
        }))
    assert ledger.record_position_closed(**close)
    recovered = ExitProtectionLedger(DurableJournal(path), position_id=POSITION_ID)
    assert recovered.state.open_quantity == 0
    assert recovered.state.closed_event_id == close["event_id"]
    assert not _record_exit_fill(recovered, exit_key="full-exit", fill_id="close-fill-2",
                                 quantity=Decimal("5"), idempotency_key="exit:close:2")
    assert not recovered.record_position_closed(**close)


def test_entry_decision_hash_binds_strategy_and_snapshot(tmp_path):
    hashes = []
    for index, snapshot in enumerate(({"setup": "breakout"}, {"setup": "reversal"})):
        position_id = f"550e8400-e29b-41d4-a716-44665544000{index + 1}"
        signal_id = f"550e8400-e29b-41d4-a716-44665544000{index + 4}"
        entry = EntrySignal(
            signal_id=signal_id, opens_position_id=position_id, strategy_id="breakout",
            strategy_version="1", instrument_id="NSE:INFY", side=EntrySide.BUY,
            quote_source_id="kite:NSE", source_hash="a" * 64, occurred_at=NOW,
        )
        ledger = ExitProtectionLedger(DurableJournal(tmp_path / f"decision-{index}.journal"),
                                      position_id=position_id)
        ledger.record_entry_signal(entry, snapshot=snapshot, idempotency_key=f"entry:{index}")
        hashes.append(ledger.journal.records()[0]["decision_sha256"])
    assert hashes[0] != hashes[1]
