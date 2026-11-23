from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.signals.position_signals import (EntryAction, EntrySignal, ExitAction,
                                                       ExitSignal, ExitTrigger,
                                                       PositionSignalError)

NOW = datetime(2026, 11, 23, 10, tzinfo=timezone.utc)


def entry(action=EntryAction.BUY):
    return EntrySignal("00000000-0000-0000-0000-000000000001",
                       "00000000-0000-0000-0000-000000000002", "trend", "v1",
                       "NSE:INFY", action, "kite:quote-1", NOW)


def test_exit_links_position_entry_and_correct_closing_side():
    long_exit = ExitSignal.for_entry(entry(), signal_id="00000000-0000-0000-0000-000000000003",
                                     trigger=ExitTrigger.STOP, quote_source_id="kite:q2",
                                     occurred_at=NOW + timedelta(minutes=1))
    short_exit = ExitSignal.for_entry(entry(EntryAction.SELL_SHORT),
                                      signal_id="00000000-0000-0000-0000-000000000004",
                                      trigger=ExitTrigger.TIME, quote_source_id="kite:q3",
                                      occurred_at=NOW + timedelta(minutes=2))
    assert long_exit.action == ExitAction.SELL_TO_CLOSE
    assert long_exit.closes_position_id == entry().opens_position_id
    assert long_exit.parent_entry_signal_id == entry().signal_id
    long_exit.validate_against(entry())
    assert short_exit.action == ExitAction.BUY_TO_COVER


def test_invalid_ids_and_exit_before_entry_are_rejected():
    with pytest.raises(PositionSignalError, match="UUID"):
        EntrySignal("bad", "bad", "s", "v", "NSE:X", EntryAction.BUY, "quote", NOW)
    with pytest.raises(PositionSignalError, match="precede"):
        ExitSignal.for_entry(entry(), signal_id="00000000-0000-0000-0000-000000000003",
                             trigger=ExitTrigger.MANUAL, quote_source_id="quote",
                             occurred_at=NOW - timedelta(seconds=1))
