"""Exchange-session holding-period exit schedules for forward paper books."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import hashlib
import json
from collections.abc import Iterable


class HoldingScheduleError(ValueError):
    """Invalid session calendar or an unresolvable frozen exit session."""


@dataclass(frozen=True)
class ExchangeSessionCalendar:
    """Caller-supplied exchange sessions and identity of the calendar source."""

    calendar_id: str
    session_dates: tuple[date, ...]
    source_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.calendar_id, str) or not self.calendar_id.strip():
            raise HoldingScheduleError("exchange calendar identity is required")
        if not isinstance(self.session_dates, tuple) or not self.session_dates:
            raise HoldingScheduleError("calendar session dates must be a non-empty tuple")
        if any(type(day) is not date for day in self.session_dates):
            raise HoldingScheduleError("calendar dates must be exchange-session dates")
        if tuple(sorted(set(self.session_dates))) != self.session_dates:
            raise HoldingScheduleError("calendar dates must be unique and increasing")
        if (not isinstance(self.source_sha256, str) or len(self.source_sha256) != 64
                or any(char not in "0123456789abcdef" for char in self.source_sha256)):
            raise HoldingScheduleError("calendar source must have a lowercase SHA-256 identity")


@dataclass(frozen=True)
class HoldingExitSchedule:
    entry_session: date
    as_of_session: date
    scheduled_exit_session: date
    horizon_sessions: int
    elapsed_sessions: int
    remaining_sessions: int
    status: str
    exit_window: str
    calendar_id: str
    calendar_source_sha256: str


def schedule_holding_exit(*, entry_session: date, horizon_sessions: int, as_of_session: date,
                          calendar: ExchangeSessionCalendar) -> HoldingExitSchedule:
    """Resolve the frozen exit session by exchange sessions, never calendar days or wall-clock resets.

    A one-session horizon exits on the next exchange session after entry, matching the paper
    book's `walk` convention. The schedule identifies an exchange-close execution window;
    actual order timing and fills remain the paper adapter's responsibility.
    """
    if type(entry_session) is not date or type(as_of_session) is not date:
        raise HoldingScheduleError("entry and as-of dates must be exchange session dates")
    if type(horizon_sessions) is not int or horizon_sessions < 1:
        raise HoldingScheduleError("holding horizon must be a positive session count")
    try:
        entry_index = calendar.session_dates.index(entry_session)
        as_of_index = calendar.session_dates.index(as_of_session)
    except ValueError as exc:
        raise HoldingScheduleError("entry and as-of dates must exist in the supplied exchange calendar") from exc
    if as_of_index < entry_index:
        raise HoldingScheduleError("as-of session cannot precede the position entry session")
    exit_index = entry_index + horizon_sessions
    if exit_index >= len(calendar.session_dates):
        raise HoldingScheduleError("calendar does not cover the frozen holding horizon exit session")
    target = calendar.session_dates[exit_index]
    elapsed = as_of_index - entry_index
    if target > as_of_session:
        status = "SCHEDULED"
    elif target == as_of_session:
        status = "DUE"
    else:
        status = "OVERDUE"
    return HoldingExitSchedule(
        entry_session=entry_session,
        as_of_session=as_of_session,
        scheduled_exit_session=target,
        horizon_sessions=horizon_sessions,
        elapsed_sessions=elapsed,
        remaining_sessions=max(0, horizon_sessions - elapsed),
        status=status,
        exit_window="EXCHANGE_CLOSE",
        calendar_id=calendar.calendar_id,
        calendar_source_sha256=calendar.source_sha256,
    )


def scheduled_exit_order_intents(position_events: Iterable[object], *, exchange_session: date) -> list[dict]:
    """Build deterministic SELL-to-close intents only for due/overdue scheduled positions."""
    if type(exchange_session) is not date:
        raise HoldingScheduleError("current exchange session date is required")
    intents = []
    for event in position_events:
        if getattr(event, "kind", None) != "BOOK_POSITION":
            continue
        payload = dict(getattr(event, "payload", ()))
        state = payload.get("position_state")
        if state not in {"EXIT_DUE", "EXIT_OVERDUE"}:
            continue
        try:
            target = date.fromisoformat(payload["scheduled_exit_session"])
            shares = int(payload["shares"])
        except (KeyError, TypeError, ValueError) as exc:
            raise HoldingScheduleError("due position needs a valid scheduled session and quantity") from exc
        if target > exchange_session or (state == "EXIT_DUE" and target != exchange_session):
            raise HoldingScheduleError("position due state conflicts with the current exchange session")
        if state == "EXIT_OVERDUE" and target >= exchange_session:
            raise HoldingScheduleError("overdue state requires an earlier target exchange session")
        if shares <= 0:
            raise HoldingScheduleError("due position has no executable share quantity")
        identity = {
            "instrument": getattr(event, "instrument", ""),
            "book_version": payload.get("book_version"), "mode": payload.get("mode"),
            "signal_day": payload.get("signal_day"), "entry_day": payload.get("entry_day"),
            "scheduled_exit_session": target.isoformat(),
        }
        if not identity["instrument"] or not identity["entry_day"]:
            raise HoldingScheduleError("due position identity is incomplete")
        order_id = hashlib.sha256(json.dumps(
            identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        intents.append({
            "order_id": order_id, "instrument": identity["instrument"],
            "side": "SELL", "position_effect": "CLOSE", "quantity": shares,
            "reason": "TIME_EXIT" if state == "EXIT_DUE" else "OVERDUE_TIME_EXIT",
            "exchange_session": exchange_session.isoformat(),
            "scheduled_exit_session": target.isoformat(),
            "exit_window": payload.get("exit_window"),
            "calendar_id": payload.get("exit_calendar_id"),
            "calendar_source_sha256": payload.get("exit_calendar_source_sha256"),
            "position_identity": identity,
        })
    return intents
