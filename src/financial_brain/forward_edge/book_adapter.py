"""Read-only horizon-aware adapter from durable daily paper books to forward sessions."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from financial_brain.forward_edge.market_data import DataError, NormalizedEvent
from financial_brain.forward_edge.holding_period_schedule import (
    ExchangeSessionCalendar, HoldingScheduleError, schedule_holding_exit,
)
from financial_brain.forward_edge.session_clock import ClockError, SessionClockProvider
from financial_brain.paper.book import Spec

IST = timezone(timedelta(hours=5, minutes=30))


class BookAdapterError(ValueError):
    """Invalid or inconsistent persisted position state."""


def forward_position_events(book, *, mode: str, spec: Spec, as_of: date,
                            clock_provider: SessionClockProvider | None = None,
                            session_calendar: ExchangeSessionCalendar | None = None) -> list[NormalizedEvent]:
    """Expose existing open positions to a runner without creating or settling fills.

    The input is the daily book's persisted `positions` table, not an intraday callback.
    Positions remain ACTIVE across sessions while `held < horizon`; a row still open at
    its frozen horizon is emitted as EXIT_DUE for end-of-session reconciliation.
    Unfilled rows remain ENTRY_PENDING and never become assumed positions.
    """
    if not mode or type(as_of) is not date:
        raise BookAdapterError("mode and exchange session date are required")
    clock = clock_provider or SessionClockProvider()
    try:
        rows = book.execute(
            """SELECT signal_day, ticker, horizon, entry_day, entry_px, last_day, last_px,
                      held, stop, shares, exit_day
               FROM positions WHERE version = ? AND mode = ? AND exit_day IS NULL
               ORDER BY signal_day, ticker""",
            [spec.version, mode],
        ).fetchall()
    except Exception as exc:
        raise BookAdapterError("daily paper book positions schema is unavailable") from exc

    output: list[NormalizedEvent] = []
    for signal_day, ticker, row_horizon, entry_day, entry_px, last_day, last_px, held, stop, shares, exit_day in rows:
        if exit_day is not None:
            continue
        if not isinstance(ticker, str) or not ticker.strip():
            raise BookAdapterError("paper book position has no instrument")
        horizon = int(row_horizon if row_horizon is not None else (spec.horizon or 0))
        held_sessions = int(held or 0)
        if horizon < 1 or held_sessions < 0:
            raise BookAdapterError("position horizon and completed holding sessions must be positive")
        if held_sessions > horizon:
            raise BookAdapterError("position holding sessions exceed its frozen horizon")
        if last_day is not None and last_day > as_of:
            raise BookAdapterError("paper book contains a position mark after the requested session")
        exit_schedule = None
        if entry_day is not None and entry_day > as_of:
            state = "ENTRY_PENDING"
        elif entry_day is None:
            state = "ENTRY_PENDING"
        elif session_calendar is not None:
            try:
                exit_schedule = schedule_holding_exit(
                    entry_session=entry_day, horizon_sessions=horizon,
                    as_of_session=as_of, calendar=session_calendar,
                )
                marked_session = last_day or entry_day
                marked_held = (session_calendar.session_dates.index(marked_session)
                               - session_calendar.session_dates.index(entry_day))
            except (HoldingScheduleError, ValueError) as exc:
                raise BookAdapterError(f"position exit schedule cannot be resolved: {exc}") from exc
            if held_sessions != marked_held:
                raise BookAdapterError("paper-book held-session count does not match its exchange-session dates")
            state = {"SCHEDULED": "ACTIVE", "DUE": "EXIT_DUE", "OVERDUE": "EXIT_OVERDUE"}[exit_schedule.status]
        else:
            state = "EXIT_DUE" if held_sessions == horizon else "ACTIVE"
        source_day = last_day or entry_day or signal_day
        if source_day is None or source_day > as_of:
            raise BookAdapterError("position has no source date at or before the requested session")
        exchange_utc = datetime.combine(source_day, time(15, 30), tzinfo=IST).astimezone(timezone.utc)
        try:
            clocks = clock.event_clocks(exchange_utc)
            ingested_utc = clock.now_utc("ingest")
        except ClockError as exc:
            raise BookAdapterError(str(exc)) from exc
        if ingested_utc < clocks.available_utc:
            raise BookAdapterError("ingest clock cannot precede book-state availability")
        if exit_schedule is not None:
            schedule_available = datetime.combine(as_of, time(8, 45), tzinfo=IST).astimezone(timezone.utc)
            if ingested_utc < schedule_available:
                raise BookAdapterError("exit schedule clock precedes the scheduled exchange session")
        instrument = ticker if ":" in ticker else f"NSE:{ticker}"
        payload = {
            "source": "daily_paper_book",
            "book_version": spec.version,
            "mode": mode,
            "signal_day": signal_day.isoformat() if signal_day else "",
            "entry_day": entry_day.isoformat() if entry_day else "",
            "entry_price": str(entry_px) if entry_px is not None else "",
            "last_day": last_day.isoformat() if last_day else "",
            "last_price": str(last_px) if last_px is not None else "",
            "held_sessions": str(held_sessions),
            "horizon_sessions": str(horizon),
            "remaining_sessions": str(exit_schedule.remaining_sessions if exit_schedule else
                                       max(0, horizon - held_sessions)),
            "stop_price": str(stop) if stop is not None else "",
            "shares": str(int(shares or 0)),
            "position_state": state,
            "decision_clock": "DAILY_SESSION_ONLY",
        }
        if exit_schedule is not None:
            payload.update({
                "scheduled_exit_session": exit_schedule.scheduled_exit_session.isoformat(),
                "exit_schedule_status": exit_schedule.status,
                "exit_window": exit_schedule.exit_window,
                "exit_calendar_id": exit_schedule.calendar_id,
                "exit_calendar_source_sha256": exit_schedule.calendar_source_sha256,
                "holding_clock": "EXCHANGE_SESSIONS_FROM_ENTRY",
            })
        try:
            output.append(NormalizedEvent(
                kind="BOOK_POSITION", instrument=instrument,
                event_utc=exchange_utc, received_utc=clocks.received_utc,
                available_utc=clocks.available_utc,
                payload=tuple(sorted(payload.items())), ingested_utc=ingested_utc,
            ))
        except DataError as exc:
            raise BookAdapterError(str(exc)) from exc
    return output
