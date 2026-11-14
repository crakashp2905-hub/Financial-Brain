"""Auditable planned/completed session denominators across exchange calendars."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from financial_brain.forward_edge.durable import DurableJournal
from financial_brain.forward_edge.market_data import Calendar
from financial_brain.forward_edge.session_ops import SessionDateLedger

IST = ZoneInfo("Asia/Kolkata")


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


class FrozenSessionScheduleRegistry:
    """Append-only authority that freezes a cohort schedule before its first session day."""

    def __init__(self, path: Path) -> None:
        self.journal = DurableJournal(Path(path))

    def register(self, schedule: FrozenSessionSchedule, *, frozen_at_utc: datetime) -> dict:
        if not isinstance(schedule, FrozenSessionSchedule):
            raise SessionCoverageError("a validated frozen session schedule is required")
        if frozen_at_utc.tzinfo is None or frozen_at_utc.utcoffset() is None:
            raise SessionCoverageError("schedule freeze clock must be timezone-aware")
        frozen_at = frozen_at_utc.astimezone(timezone.utc)
        prior = next((row for row in self.journal.records()
                      if row.get("t") == "SESSION_SCHEDULE_FROZEN"
                      and row.get("schedule_id") == schedule.schedule_id), None)
        if prior is not None:
            if (prior.get("schedule") != schedule.canonical()
                    or prior.get("schedule_sha256") != schedule.sha256
                    or prior.get("frozen_at_utc") != frozen_at.isoformat()):
                raise SessionCoverageError("registered session schedule is immutable")
            return {"schedule_id": schedule.schedule_id, "schedule_sha256": schedule.sha256,
                    "frozen_at_utc": frozen_at.isoformat(), "registered": True}
        first_session_start = datetime.combine(
            date.fromisoformat(schedule.sessions[0].exchange_date), datetime.min.time(), tzinfo=IST,
        ).astimezone(timezone.utc)
        if frozen_at >= first_session_start:
            raise SessionCoverageError("schedule must be frozen before the first exchange session date")
        record = {"t": "SESSION_SCHEDULE_FROZEN", "schedule_id": schedule.schedule_id,
                  "schedule": schedule.canonical(), "schedule_sha256": schedule.sha256,
                  "frozen_at_utc": frozen_at.isoformat()}
        self.journal.append(record)
        return {"schedule_id": schedule.schedule_id, "schedule_sha256": schedule.sha256,
                "frozen_at_utc": frozen_at.isoformat(), "registered": True}

    def resolve(self, schedule_id: str) -> tuple[FrozenSessionSchedule, dict]:
        records = [row for row in self.journal.records()
                   if row.get("t") == "SESSION_SCHEDULE_FROZEN"
                   and row.get("schedule_id") == schedule_id]
        if len(records) != 1:
            raise SessionCoverageError("exactly one frozen schedule registration is required")
        record = records[0]
        try:
            payload = record["schedule"]
            schedule = FrozenSessionSchedule(
                payload["schedule_id"], payload["calendar_version"],
                tuple(PlannedSession(row["session_id"], row["exchange_date"],
                                     row["settlement_deadline_utc"])
                      for row in payload["sessions"]),
            )
            frozen_at = datetime.fromisoformat(record["frozen_at_utc"])
            if (schedule.sha256 != record["schedule_sha256"]
                    or schedule.schedule_id != schedule_id
                    or frozen_at.tzinfo is None or frozen_at.utcoffset() is None):
                raise ValueError
        except (KeyError, TypeError, ValueError) as exc:
            raise SessionCoverageError("registered schedule evidence is invalid") from exc
        return schedule, {"schedule_sha256": schedule.sha256,
                          "frozen_at_utc": frozen_at.astimezone(timezone.utc).isoformat()}


def session_coverage_report(*, registry: FrozenSessionScheduleRegistry, schedule_id: str,
                            ledger: SessionDateLedger,
                            calendar: Calendar, window_start: date, window_end: date,
                            as_of_utc: datetime) -> dict:
    """Report all frozen denominator rows plus weekend/holiday exclusions and missing sessions."""
    schedule, registration = registry.resolve(schedule_id)
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
        "schedule_frozen_at_utc": registration["frozen_at_utc"],
        "window_start": window_start.isoformat(), "window_end": window_end.isoformat(),
        "as_of_utc": now.isoformat(), "planned_session_denominator": len(schedule.sessions),
        "terminal_session_count": sum(row["terminal"] is True for row in sessions),
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
