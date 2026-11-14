"""Auditable planned/completed session denominators across exchange calendars."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone

from financial_brain.forward_edge.market_data import Calendar
from financial_brain.forward_edge.session_ops import SessionDateLedger


class SessionCoverageError(ValueError):
    """A frozen schedule or its ledger evidence cannot support an auditable denominator."""


def _canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


@dataclass(frozen=True)
class PlannedSession:
    session_id: str
    exchange_date: str
    settlement_deadline_utc: str

    def __post_init__(self) -> None:
        if not isinstance(self.session_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,96}", self.session_id):
            raise SessionCoverageError("session id must be a stable token")
        try:
            if date.fromisoformat(self.exchange_date).isoformat() != self.exchange_date:
                raise ValueError
            deadline = datetime.fromisoformat(self.settlement_deadline_utc)
            if deadline.tzinfo is None or deadline.utcoffset() is None:
                raise ValueError
        except (TypeError, ValueError) as exc:
            raise SessionCoverageError("planned session date and aware UTC deadline are required") from exc
        if deadline.astimezone(timezone.utc).isoformat() != self.settlement_deadline_utc:
            raise SessionCoverageError("settlement deadline must be canonical UTC ISO format")

    def canonical(self) -> dict:
        return {"session_id": self.session_id, "exchange_date": self.exchange_date,
                "settlement_deadline_utc": self.settlement_deadline_utc}


@dataclass(frozen=True)
class FrozenSessionSchedule:
    schedule_id: str
    calendar_version: str
    sessions: tuple[PlannedSession, ...]

    def __post_init__(self) -> None:
        if not self.schedule_id.strip() or not self.calendar_version.strip() or not self.sessions:
            raise SessionCoverageError("schedule id, calendar version and sessions are required")
        if len({row.session_id for row in self.sessions}) != len(self.sessions):
            raise SessionCoverageError("schedule session ids must be unique")
        ordered = tuple(sorted(self.sessions, key=lambda row: (row.exchange_date, row.session_id)))
        if ordered != self.sessions:
            raise SessionCoverageError("schedule sessions must be in exchange-date order")

    def canonical(self) -> dict:
        return {"schedule_id": self.schedule_id, "calendar_version": self.calendar_version,
                "sessions": [row.canonical() for row in self.sessions]}

    @property
    def sha256(self) -> str:
        return hashlib.sha256(_canonical(self.canonical())).hexdigest()


def session_coverage_report(*, schedule: FrozenSessionSchedule, ledger: SessionDateLedger,
                            calendar: Calendar, window_start: date, window_end: date,
                            as_of_utc: datetime) -> dict:
    """Report all frozen denominator rows plus weekend/holiday exclusions and missing sessions."""
    if schedule.calendar_version != calendar.version:
        raise SessionCoverageError("frozen schedule calendar version differs from the supplied calendar")
    if type(window_start) is not date or type(window_end) is not date or window_end < window_start:
        raise SessionCoverageError("coverage window dates are invalid")
    if as_of_utc.tzinfo is None or as_of_utc.utcoffset() is None:
        raise SessionCoverageError("coverage report clock must be timezone-aware")
    now = as_of_utc.astimezone(timezone.utc)
    for row in schedule.sessions:
        day = date.fromisoformat(row.exchange_date)
        if not window_start <= day <= window_end:
            raise SessionCoverageError("frozen schedule contains a session outside the report window")
        if not calendar.is_tradable(day):
            raise SessionCoverageError("frozen schedule includes a weekend or holiday as a market session")
    records = ledger.records()
    plans = {row.get("session_id"): row for row in records if row.get("t") == "SESSION_DATE_PLAN"}
    outcomes = {row.get("session_id"): row for row in records if row.get("t") == "SESSION_DATE_RESULT"}
    sessions = []
    for expected_row in schedule.sessions:
        plan = plans.get(expected_row.session_id)
        outcome = outcomes.get(expected_row.session_id)
        if plan is None:
            status, reason = "UNPLANNED", "SESSION_DATE_PLAN_MISSING"
        elif (plan.get("exchange_date") != expected_row.exchange_date
              or plan.get("settlement_deadline_utc") != expected_row.settlement_deadline_utc
              or plan.get("calendar_version") != schedule.calendar_version
              or plan.get("tradable") is not True):
            status, reason = "PLAN_MISMATCH", "FROZEN_PLAN_DIFFERS_FROM_LEDGER"
        elif outcome is not None:
            status, reason = outcome.get("status", "UNKNOWN"), outcome.get("reason", "REASON_MISSING")
            if status not in {"CLOSED", "NO_TRADE", "INCOMPLETE"}:
                status, reason = "UNQUALIFIED", "UNKNOWN_TERMINAL_STATUS"
        elif datetime.fromisoformat(expected_row.settlement_deadline_utc) <= now:
            status, reason = "MISSING", "SESSION_RESULT_MISSING_AFTER_DEADLINE"
        else:
            status, reason = "PENDING", "SETTLEMENT_DEADLINE_NOT_REACHED"
        sessions.append({"session_id": expected_row.session_id,
                         "exchange_date": expected_row.exchange_date,
                         "status": status, "reason": reason,
                         "planned": plan is not None,
                         "terminal": status in {"CLOSED", "NO_TRADE", "INCOMPLETE"}})
    excluded = []
    day = window_start
    while day <= window_end:
        if not calendar.is_tradable(day):
            excluded.append({"exchange_date": day.isoformat(),
                             "reason": "WEEKEND" if day.weekday() >= 5 else "CALENDAR_HOLIDAY"})
        day = date.fromordinal(day.toordinal() + 1)
    expected_dates = {row.exchange_date for row in schedule.sessions}
    unscheduled_market_dates = [day.isoformat() for day in (
        date.fromordinal(window_start.toordinal() + offset)
        for offset in range((window_end - window_start).days + 1)
    ) if calendar.is_tradable(day) and day.isoformat() not in expected_dates]
    status_counts = {key: sum(row["status"] == key for row in sessions)
                     for key in ("CLOSED", "NO_TRADE", "INCOMPLETE", "MISSING", "PENDING",
                                 "UNPLANNED", "PLAN_MISMATCH", "UNQUALIFIED")}
    unqualified = (status_counts["INCOMPLETE"] + status_counts["MISSING"]
                   + status_counts["UNPLANNED"] + status_counts["PLAN_MISMATCH"]
                   + status_counts["UNQUALIFIED"])
    body = {
        "schema_version": "1.0", "schedule_id": schedule.schedule_id,
        "schedule_sha256": schedule.sha256, "calendar_version": calendar.version,
        "window_start": window_start.isoformat(), "window_end": window_end.isoformat(),
        "as_of_utc": now.isoformat(), "planned_session_denominator": len(schedule.sessions),
        "terminal_session_count": sum(row["terminal"] for row in sessions),
        "qualified_closed_count": status_counts["CLOSED"],
        "no_trade_count": status_counts["NO_TRADE"],
        "unqualified_count": unqualified, "status_counts": status_counts,
        "excluded_non_trading_dates": excluded,
        "unscheduled_tradable_dates": unscheduled_market_dates,
        "sessions": sessions,
    }
    lines = [
        "Financial Brain — Planned Session Coverage",
        f"Schedule: {schedule.schedule_id} ({schedule.sha256})",
        f"Exchange calendar: {calendar.version}",
        f"Window: {window_start.isoformat()} through {window_end.isoformat()}",
        f"Planned denominator: {len(schedule.sessions)}",
        f"Closed/qualified: {status_counts['CLOSED']}; NO_TRADE: {status_counts['NO_TRADE']}; "
        f"incomplete/missing/unqualified: {unqualified}; pending: {status_counts['PENDING']}",
        "Sessions:",
    ]
    lines.extend(f"  {row['exchange_date']} | {row['session_id']} | {row['status']} | {row['reason']}"
                 for row in sessions)
    lines.append("Excluded non-trading dates: " + (
        ", ".join(f"{row['exchange_date']}={row['reason']}" for row in excluded) or "none"))
    lines.append("Unscheduled tradable dates: " + (", ".join(unscheduled_market_dates) or "none"))
    body["human_report"] = "\n".join(lines) + "\n"
    body["report_sha256"] = hashlib.sha256(_canonical(body)).hexdigest()
    return body
