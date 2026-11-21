from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.kernel.position_signals import (
    ClosingAction, EntrySide, EntrySignal, ExitSignal, PositionSignalError,
    validate_entry_exit_link,
)


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
