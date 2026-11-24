from datetime import date, datetime, timedelta, timezone

import pytest

from financial_brain.forward_edge.harm_stop import (
    HarmSession,
    HarmStopError,
    HarmStopPlan,
    evaluate_harm_stop,
)


def _plan(*, daily=0.10, drawdown=0.20, minimum=60):
    return HarmStopPlan("harm-v1", datetime(2026, 11, 15, 10, tzinfo=timezone.utc),
                        date(2026, 11, 16), daily, drawdown, minimum)


def _session(day, ret=0.0, *, reconciled=True, incident=None):
    return HarmSession(day, ret, reconciled, incident)


def test_daily_loss_reconciliation_and_incident_triggers_stop_at_first_harm() -> None:
    start = date(2026, 11, 16)
    loss = evaluate_harm_stop(_plan(), (_session(start, -0.11), _session(start + timedelta(days=1))))
    assert loss.status == "STOP_FOR_HARM"
    assert loss.evaluated_sessions == 1
    assert loss.trigger_reason == "MAX_DAILY_LOSS_BREACHED"
    mismatch = evaluate_harm_stop(_plan(), (_session(start, 0.0, reconciled=False),))
    assert mismatch.trigger_reason == "LEDGER_RECONCILIATION_MISMATCH"
    incident = evaluate_harm_stop(_plan(), (_session(start, 0.0, incident="ledger-alert-1"),))
    assert incident.trigger_reason == "CRITICAL_INCIDENT:ledger-alert-1"
    assert not loss.promotion_allowed


def test_drawdown_rule_is_independent_from_single_day_loss_rule() -> None:
    start = date(2026, 11, 16)
    plan = _plan(daily=0.50, drawdown=0.10)
    result = evaluate_harm_stop(plan, (_session(start, 0.10), _session(start + timedelta(days=1), -0.20)))
    assert result.status == "STOP_FOR_HARM"
    assert result.trigger_reason == "MAX_DRAWDOWN_BREACHED"
    assert result.trigger_session == start + timedelta(days=1)


def test_no_harm_continues_or_requests_review_without_promoting() -> None:
    start = date(2026, 11, 16)
    early = evaluate_harm_stop(_plan(minimum=3), (_session(start, 0.01), _session(start + timedelta(days=1), 0)))
    assert early.status == "CONTINUE"
    complete = evaluate_harm_stop(_plan(minimum=2), (_session(start, 0.01), _session(start + timedelta(days=1), 0)))
    assert complete.status == "REVIEW_ONLY"
    assert not complete.promotion_allowed


def test_plan_freeze_and_session_order_are_enforced() -> None:
    start = date(2026, 11, 16)
    with pytest.raises(HarmStopError, match="before"):
        HarmStopPlan("late", datetime(2026, 11, 16, tzinfo=timezone.utc), start, 0.1, 0.2)
    rows = (_session(start + timedelta(days=1)), _session(start))
    with pytest.raises(HarmStopError, match="chronological"):
        evaluate_harm_stop(_plan(), rows)
