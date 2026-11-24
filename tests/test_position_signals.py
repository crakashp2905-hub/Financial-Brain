from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from financial_brain.kernel.position_signals import (
    ClosingAction, EmittedExitEvent, EntrySide, EntrySignal, ExitProtectionIntent,
    ExitSignal, PositionSignalError, validate_entry_exit_link, validate_protection_emission,
)
from financial_brain.kernel.trader_signal import ExitTrigger


def _entry(side=EntrySide.BUY):
    return EntrySignal(
        signal_id="550e8400-e29b-41d4-a716-446655440000",
        opens_position_id="550e8400-e29b-41d4-a716-446655440001",
        strategy_id="breakout", strategy_version="2", instrument_id="NSE:INFY",
        side=side, quote_source_id="kite:NSE", source_hash="a" * 64,
        occurred_at=datetime(2026, 11, 21, 10, tzinfo=timezone.utc),
    )


def _exit(action=ClosingAction.SELL_TO_CLOSE, **changes):
    values = {
        "signal_id": "550e8400-e29b-41d4-a716-446655440002",
        "closes_position_id": "550e8400-e29b-41d4-a716-446655440001",
        "strategy_id": "breakout", "strategy_version": "2", "instrument_id": "NSE:INFY",
        "closing_action": action, "quote_source_id": "kite:NSE", "source_hash": "b" * 64,
        "occurred_at": datetime(2026, 11, 21, 11, tzinfo=timezone.utc),
        "parent_signal_id": "550e8400-e29b-41d4-a716-446655440000", "reason": "target reached",
    }
    return ExitSignal(**(values | changes))


@pytest.mark.parametrize("side, action", [
    (EntrySide.BUY, ClosingAction.SELL_TO_CLOSE),
    (EntrySide.SELL_SHORT, ClosingAction.BUY_TO_COVER),
    (EntrySide.MF_SUBSCRIBE, ClosingAction.MF_REDEEM),
    (EntrySide.MF_SUBSCRIBE, ClosingAction.MF_SWITCH),
])
def test_entry_exit_lineage_and_closing_action_match(side, action):
    entry = _entry(side)
    validate_entry_exit_link(entry, _exit(action))


def test_exit_rejects_mismatched_position_parent_side_and_time():
    entry = _entry()
    with pytest.raises(PositionSignalError, match="different position"):
        validate_entry_exit_link(entry, _exit(closes_position_id="550e8400-e29b-41d4-a716-446655440099"))
    with pytest.raises(PositionSignalError, match="parent"):
        validate_entry_exit_link(entry, _exit(parent_signal_id="550e8400-e29b-41d4-a716-446655440099"))
    with pytest.raises(PositionSignalError, match="reverse"):
        validate_entry_exit_link(entry, _exit(ClosingAction.BUY_TO_COVER))
    with pytest.raises(PositionSignalError, match="precede"):
        validate_entry_exit_link(entry, _exit(occurred_at=entry.occurred_at - timedelta(seconds=1)))


def _protection(**changes):
    values = {
        "protection_id": "550e8400-e29b-41d4-a716-446655440003",
        "position_id": "550e8400-e29b-41d4-a716-446655440001",
        "parent_entry_signal_id": "550e8400-e29b-41d4-a716-446655440000",
        "trigger": ExitTrigger.HARD_STOP, "trigger_price": Decimal("95"),
        "quantity": Decimal("10"), "created_at": datetime(2026, 11, 21, 10, 1, tzinfo=timezone.utc),
        "source_hash": "c" * 64,
    }
    return ExitProtectionIntent(**(values | changes))


def _emission(**changes):
    values = {
        "exit_signal": _exit(reason="hard stop observed"),
        "protection_id": "550e8400-e29b-41d4-a716-446655440003",
        "trigger": ExitTrigger.HARD_STOP, "idempotency_key": "position:1:hard-stop:1",
        "quantity": Decimal("4"), "observed_trigger_at": datetime(2026, 11, 21, 10, 59, tzinfo=timezone.utc),
    }
    return EmittedExitEvent(**(values | changes))


def test_proposed_protection_is_distinct_and_can_emit_timestamped_exit():
    entry, protection, event = _entry(), _protection(), _emission()
    validate_protection_emission(entry, protection, event, open_quantity=Decimal("7"))
    assert protection.trigger is ExitTrigger.HARD_STOP
    assert event.exit_signal.occurred_at >= event.observed_trigger_at
    assert event.quantity < protection.quantity


@pytest.mark.parametrize("changes, message", [
    ({"protection_id": "550e8400-e29b-41d4-a716-446655440004"}, "differs from its protection"),
    ({"trigger": ExitTrigger.TAKE_PROFIT}, "differs from its protection"),
    ({"idempotency_key": " "}, "idempotency_key"),
])
def test_emitted_exit_requires_protection_identity_and_idempotency(changes, message):
    if "idempotency_key" in changes:
        with pytest.raises(PositionSignalError, match=message):
            _emission(**changes)
    else:
        with pytest.raises(PositionSignalError, match=message):
            validate_protection_emission(_entry(), _protection(), _emission(**changes),
                                         open_quantity=Decimal("10"))


def test_emitted_exit_cannot_over_close_open_or_protected_quantity():
    with pytest.raises(PositionSignalError, match="open position"):
        validate_protection_emission(_entry(), _protection(), _emission(), open_quantity=Decimal("3"))
    oversized = _emission(quantity=Decimal("11"))
    with pytest.raises(PositionSignalError, match="open position"):
        validate_protection_emission(_entry(), _protection(), oversized, open_quantity=Decimal("10"))


def test_protection_and_event_reject_naive_times_and_bad_hashes():
    with pytest.raises(PositionSignalError, match="timezone-aware"):
        _protection(created_at=datetime(2026, 11, 21, 10))
    with pytest.raises(PositionSignalError, match="SHA-256"):
        _protection(source_hash="bad")
