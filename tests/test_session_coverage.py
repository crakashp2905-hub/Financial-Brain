from datetime import date, datetime, timedelta, timezone
import hashlib
import json

import pytest

from financial_brain.forward_edge.market_data import Calendar
from financial_brain.forward_edge.session_coverage import (
    FrozenSessionSchedule,
    FrozenSessionScheduleRegistry,
    PlannedSession,
    SessionCoverageError,
    session_coverage_report,
)
from financial_brain.forward_edge.session_ops import SessionDateLedger


UTC = timezone.utc
AS_OF = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)


def _planned(session_id, day, deadline="2026-11-18T10:00:00+00:00"):
    return PlannedSession(session_id, day, deadline)


def _schedule(rows):
    return FrozenSessionSchedule("paper-cohort-Nov-2026", "nse-calendar-v1", tuple(rows))


def _registry(tmp_path, schedule):
    registry = FrozenSessionScheduleRegistry(tmp_path / "frozen-schedules.jsonl")
    first_midnight = datetime.fromisoformat(schedule.sessions[0].exchange_date + "T00:00:00+05:30")
    registry.register(schedule, frozen_at_utc=first_midnight.astimezone(UTC).replace(microsecond=0)
                      - timedelta(seconds=1))
    return registry


def test_report_separates_closed_no_trade_incomplete_missing_pending_and_calendar_exclusions(tmp_path):
    holiday = date(2026, 11, 16)
    special = date(2026, 11, 14)  # exchange opens on this Saturday
    calendar = Calendar("nse-calendar-v1", frozenset({holiday}), frozenset({special}))
    rows = [
        _planned("s12", "2026-11-12"),
        _planned("s13", "2026-11-13"),
        _planned("s14-special", "2026-11-14"),
        _planned("s17", "2026-11-17"),
        _planned("s18", "2026-11-18", "2026-11-19T10:00:00+00:00"),
    ]
    schedule = _schedule(rows)
    registry = _registry(tmp_path, schedule)
    ledger = SessionDateLedger(tmp_path / "coverage.jsonl")
    for row in rows:
        ledger.plan_date(row.session_id, row.exchange_date, calendar,
                         datetime.fromisoformat(row.settlement_deadline_utc))
    ledger.record_result("s12", "CLOSED", "SESSION_COMPLETE")
    ledger.record_result("s13", "NO_TRADE", "NO_SIGNAL")
    ledger.record_result("s14-special", "INCOMPLETE", "FEED_LOSS")

    report = session_coverage_report(
        registry=registry, schedule_id=schedule.schedule_id, ledger=ledger, calendar=calendar,
        window_start=date(2026, 11, 12), window_end=date(2026, 11, 18), as_of_utc=AS_OF,
    )
    assert report["planned_session_denominator"] == 5
    assert report["status_counts"] == {
        "CLOSED": 1, "NO_TRADE": 1, "INCOMPLETE": 1, "MISSING": 1,
        "PENDING": 1, "UNPLANNED": 0, "PLAN_MISMATCH": 0, "UNQUALIFIED": 0,
    }
    assert report["qualified_closed_count"] == 1
    assert report["no_trade_count"] == 1
    assert report["unqualified_count"] == 2  # incomplete + overdue missing
    assert report["excluded_non_trading_dates"] == [
        {"exchange_date": "2026-11-15", "reason": "WEEKEND"},
        {"exchange_date": "2026-11-16", "reason": "CALENDAR_HOLIDAY"},
    ]
    assert report["unscheduled_tradable_dates"] == []
    unsigned = {key: value for key, value in report.items() if key != "report_sha256"}
    assert report["report_sha256"] == hashlib.sha256(
        json.dumps(unsigned, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    assert "NO_TRADE: 1" in report["human_report"]
    assert "s17 | MISSING" in report["human_report"]


def test_missing_plan_and_unplanned_tradable_dates_are_not_hidden(tmp_path):
    calendar = Calendar("nse-calendar-v1", frozenset())
    schedule = _schedule([_planned("expected", "2026-11-17")])
    registry = _registry(tmp_path, schedule)
    ledger = SessionDateLedger(tmp_path / "missing-plan.jsonl")
    report = session_coverage_report(
        registry=registry, schedule_id=schedule.schedule_id, ledger=ledger, calendar=calendar,
        window_start=date(2026, 11, 17), window_end=date(2026, 11, 18), as_of_utc=AS_OF,
    )
    assert report["status_counts"]["UNPLANNED"] == 1
    assert report["unqualified_count"] == 1
    assert report["unscheduled_tradable_dates"] == ["2026-11-18"]


def test_schedule_refuses_holiday_dates_and_calendar_version_mismatch(tmp_path):
    holiday = date(2026, 11, 12)
    calendar = Calendar("nse-calendar-v1", frozenset({holiday}))
    holiday_schedule = _schedule([_planned("holiday", "2026-11-12")])
    holiday_registry = _registry(tmp_path, holiday_schedule)
    with pytest.raises(SessionCoverageError, match="weekend or holiday"):
        session_coverage_report(
            registry=holiday_registry, schedule_id=holiday_schedule.schedule_id,
            ledger=SessionDateLedger(tmp_path / "holiday.jsonl"),
            calendar=calendar, window_start=holiday, window_end=holiday, as_of_utc=AS_OF,
        )
    mismatch_schedule = FrozenSessionSchedule("s", "other-calendar", (_planned("valid", "2026-11-13"),))
    mismatch_registry = _registry(tmp_path, mismatch_schedule)
    with pytest.raises(SessionCoverageError, match="calendar version"):
        session_coverage_report(
            registry=mismatch_registry, schedule_id=mismatch_schedule.schedule_id,
            ledger=SessionDateLedger(tmp_path / "mismatch.jsonl"),
            calendar=Calendar("nse-calendar-v1", frozenset()),
            window_start=date(2026, 11, 13), window_end=date(2026, 11, 13), as_of_utc=AS_OF,
        )


def test_schedule_registry_freezes_before_session_and_rejects_retroactive_replacement(tmp_path):
    schedule = _schedule([_planned("s12", "2026-11-12")])
    path = tmp_path / "schedule-registry.jsonl"
    registry = FrozenSessionScheduleRegistry(path)
    with pytest.raises(SessionCoverageError, match="before the first"):
        registry.register(schedule, frozen_at_utc=datetime(2026, 11, 12, 0, 1, tzinfo=UTC))

    valid_freeze = datetime(2026, 11, 11, 18, 29, tzinfo=UTC)
    registered = registry.register(schedule, frozen_at_utc=valid_freeze)
    restarted = FrozenSessionScheduleRegistry(path)
    resolved, receipt = restarted.resolve(schedule.schedule_id)
    assert resolved == schedule
    assert receipt["schedule_sha256"] == registered["schedule_sha256"]
    assert receipt["frozen_at_utc"] == valid_freeze.isoformat()

    expanded = _schedule([_planned("s11", "2026-11-11"), _planned("s12", "2026-11-12")])
    with pytest.raises(SessionCoverageError, match="immutable"):
        registry.register(expanded, frozen_at_utc=valid_freeze)
