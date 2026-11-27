from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.kernel.position_signals import (
    ClosingAction,
    EmittedExitEvent,
    EntrySide,
    EntrySignal,
    ExitProtectionIntent,
    ExitSignal,
    validate_protection_emission,
)
from financial_brain.kernel.trader_signal import ExitTrigger
from financial_brain.portfolio.cadence import (
    CadenceActionKind,
    CadenceError,
    FrozenCadencePolicy,
    evaluate_cadence,
)


START = datetime(2026, 11, 25, 9, 15, tzinfo=timezone.utc)


def _event(*, observed_at: datetime, occurred_at: datetime, idempotency_key: str = "risk-1"):
    entry = EntrySignal("00000000-0000-0000-0000-000000000001",
                        "00000000-0000-0000-0000-000000000002", "strat", "v1", "NSE:ABC",
                        EntrySide.BUY, "quote-1", "a" * 64, START)
    protection = ExitProtectionIntent("00000000-0000-0000-0000-000000000003",
                                      entry.opens_position_id, entry.signal_id,
                                      ExitTrigger.RISK_KILL_SWITCH, 90, 10, START, "b" * 64)
    signal = ExitSignal("00000000-0000-0000-0000-000000000004", entry.opens_position_id,
                        entry.strategy_id, entry.strategy_version, entry.instrument_id,
                        ClosingAction.SELL_TO_CLOSE, "quote-2", "c" * 64, occurred_at,
                        entry.signal_id, "portfolio risk trigger")
    event = EmittedExitEvent(signal, protection.protection_id, protection.trigger,
                             idempotency_key, 5, observed_at)
    validate_protection_emission(entry, protection, event, open_quantity=10)
    return event


def _policy():
    return FrozenCadencePolicy(
        "swing-book", "v1", START,
        (START + timedelta(days=1), START + timedelta(days=2)), "d" * 64,
    )


def test_frozen_schedule_and_risk_exit_are_audited_with_risk_priority():
    scheduled = START + timedelta(days=1)
    event = _event(observed_at=scheduled, occurred_at=scheduled)
    now = scheduled + timedelta(days=1)
    report = evaluate_cadence(policy=_policy(), as_of=now, emitted_risk_exits=(event,))

    assert [row.kind for row in report.decisions] == [
        CadenceActionKind.EVENT_RISK_EXIT,
        CadenceActionKind.SCHEDULED_REBALANCE,
        CadenceActionKind.SCHEDULED_REBALANCE,
    ]
    assert report.decisions[0].occurred_at == scheduled
    assert report.decisions[0].position_id == event.exit_signal.closes_position_id
    assert report.decisions[0].exit_event_idempotency_key == event.idempotency_key
    assert report.decisions[0].exit_event_sha256
    assert report == evaluate_cadence(policy=_policy(), as_of=now, emitted_risk_exits=(event,))
    assert not report.promotion_allowed


def test_review_cursor_prevents_duplicate_schedule_and_exit_actions():
    scheduled = START + timedelta(days=1)
    event = _event(observed_at=scheduled, occurred_at=scheduled)
    report = evaluate_cadence(policy=_policy(), as_of=scheduled + timedelta(days=1),
                              after=scheduled, emitted_risk_exits=(event,))
    assert [row.kind for row in report.decisions] == [CadenceActionKind.SCHEDULED_REBALANCE]


def test_future_or_duplicate_exit_events_and_invalid_schedule_are_rejected():
    future = _event(observed_at=START + timedelta(days=4), occurred_at=START + timedelta(days=4))
    with pytest.raises(CadenceError, match="future risk-exit"):
        evaluate_cadence(policy=_policy(), as_of=START + timedelta(days=3), emitted_risk_exits=(future,))
    event = _event(observed_at=START + timedelta(days=1), occurred_at=START + timedelta(days=1))
    with pytest.raises(CadenceError, match="duplicate risk-exit"):
        evaluate_cadence(policy=_policy(), as_of=START + timedelta(days=2), emitted_risk_exits=(event, event))
    with pytest.raises(CadenceError, match="unique and chronological"):
        FrozenCadencePolicy("swing-book", "v1", START,
                            (START + timedelta(days=2), START + timedelta(days=1)), "e" * 64)


def test_timezone_awareness_and_review_cursor_are_required():
    with pytest.raises(CadenceError, match="timezone-aware"):
        FrozenCadencePolicy("swing-book", "v1", datetime(2026, 11, 25),
                            (START + timedelta(days=1),), "f" * 64)
    with pytest.raises(CadenceError, match="review cursor"):
        evaluate_cadence(policy=_policy(), as_of=START + timedelta(days=1),
                         after=START + timedelta(days=2))
