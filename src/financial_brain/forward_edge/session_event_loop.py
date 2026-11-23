"""Deterministic event orchestration for one sourced forward-paper session.

The loop owns ordering and lifecycle. Strategy, protective-exit, corporate-action,
and margin handlers remain explicit dependencies and must make side effects
idempotent by the supplied event ID; this module never routes a broker order.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Callable

from financial_brain.forward_edge.market_events import EventKind, MarketEvent
from financial_brain.forward_edge.session_state import ForwardRunConfig, SessionState
from financial_brain.forward_edge.venue_calendar import VenueCalendar

if TYPE_CHECKING:
    from financial_brain.forward_edge.session_runner import ForwardSessionRunner

EventHandler = Callable[[MarketEvent], None]


class SessionEventLoopError(ValueError):
    """Session events cannot safely be ordered, dispatched, or closed."""


@dataclass(frozen=True)
class SessionLoopHandlers:
    on_open: Callable[[str], None]
    on_protective_update: EventHandler
    on_completed_bar: EventHandler
    on_corporate_action: EventHandler
    on_margin_change: EventHandler
    on_auxiliary_event: EventHandler
    on_close: Callable[[str, datetime], bool]


def _utc(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise SessionEventLoopError(f"{label} must be timezone aware")
    return value.astimezone(timezone.utc)


def _priority(kind: EventKind) -> int:
    # At identical availability, state-changing/reference events precede prices;
    # continuous protective evaluation precedes completed-bar entry evaluation.
    return {
        EventKind.CORPORATE_ACTION: 0,
        EventKind.MARGIN_CHANGE: 1,
        EventKind.HALT: 2,
        EventKind.TICK: 3,
        EventKind.QUOTE: 3,
        EventKind.BOOK_DEPTH: 3,
        EventKind.BAR: 4,
        EventKind.AUCTION: 5,
        EventKind.BROKER_UPDATE: 6,
    }[kind]


def ordered_session_events(events: tuple[MarketEvent, ...] | list[MarketEvent]) -> list[MarketEvent]:
    """Return a stable availability-time order, independent of source batch ordering."""
    ids = [event.event_id for event in events]
    if len(ids) != len(set(ids)):
        raise SessionEventLoopError("session event batch contains duplicate event IDs")
    for event in events:
        _utc(event.available_at, f"event {event.event_id} availability")
    return sorted(events, key=lambda event: (
        _utc(event.available_at, f"event {event.event_id} availability"),
        _priority(event.kind),
        event.sequence if event.sequence is not None else 2**63 - 1,
        event.event_id,
    ))


def run_forward_session(
    runner: ForwardSessionRunner,
    *,
    session_id: str,
    config: ForwardRunConfig,
    venue: str,
    calendar: VenueCalendar,
    opened_at: datetime,
    closed_at: datetime,
    preflight: dict[str, bool],
    events: tuple[MarketEvent, ...] | list[MarketEvent],
    handlers: SessionLoopHandlers,
    supported_session_types: frozenset[str] = frozenset({"NORMAL"}),
) -> dict[str, object]:
    """Open, ingest, dispatch and close one deterministic paper session.

    All market data is journaled through the runner before handlers see it. An
    incomplete or unmarked bar is retained as evidence but never dispatched to
    strategy entry logic. Quotes/ticks/depth run the protective monitor as they
    arrive, independently of the completed-bar strategy cadence. A completed bar
    also runs protection before strategy evaluation. Any handler failure closes the session
    as INCOMPLETE and propagates the original error.
    """
    opened = _utc(opened_at, "opened_at")
    closed = _utc(closed_at, "closed_at")
    if closed < opened:
        raise SessionEventLoopError("closed_at cannot precede opened_at")
    session_open, session_close = calendar.session_bounds_utc(venue, config.exchange_date)
    if opened < session_open or opened >= session_close:
        raise SessionEventLoopError("opened_at must fall inside the sourced exchange session")
    ordered = ordered_session_events(events)
    if any(event.available_at.astimezone(timezone.utc) < session_open for event in ordered):
        raise SessionEventLoopError("event availability precedes the sourced exchange session open")
    if any(event.available_at.astimezone(timezone.utc) > closed for event in ordered):
        raise SessionEventLoopError("session close precedes an event availability time")

    state = runner.plan_calendar_day(
        session_id, config, venue=venue, calendar=calendar, now=opened_at,
        preflight=preflight, supported_session_types=supported_session_types,
    )
    status = state.value if isinstance(state, SessionState) else str(state)
    if status != SessionState.OPEN.value:
        return {"session_id": session_id, "status": status, "events_processed": 0,
                "completed_bars_dispatched": 0, "report": None}

    processed = 0
    bars_dispatched = 0
    try:
        handlers.on_open(session_id)
        for event in ordered:
            runner.ingest(session_id, config, [event])
            processed += 1
            if event.kind in {EventKind.TICK, EventKind.QUOTE, EventKind.BOOK_DEPTH, EventKind.HALT}:
                handlers.on_protective_update(event)
            elif event.kind is EventKind.BAR:
                is_complete = (event.payload.get("is_complete") is True
                               or event.payload.get("complete") is True)
                if is_complete:
                    handlers.on_protective_update(event)
                    handlers.on_completed_bar(event)
                    bars_dispatched += 1
            elif event.kind is EventKind.CORPORATE_ACTION:
                handlers.on_corporate_action(event)
            elif event.kind is EventKind.MARGIN_CHANGE:
                handlers.on_margin_change(event)
            else:
                handlers.on_auxiliary_event(event)

        complete = handlers.on_close(session_id, closed_at)
        report = runner.close_session(
            session_id, complete=complete, now=closed_at,
            reason=None if complete else "session close handler reported unresolved session state",
        )
    except Exception as exc:
        try:
            runner.close_session(
                session_id, complete=False, now=closed_at,
                reason=f"deterministic event loop failed: {type(exc).__name__}",
            )
        except Exception:
            # Preserve the original handler/runner failure; unresolved closure stays visible
            # through the durable runner state and must be reconciled by its normal recovery.
            pass
        raise
    return {"session_id": session_id, "status": report.get("status", "UNKNOWN"),
            "events_processed": processed, "completed_bars_dispatched": bars_dispatched,
            "report": report}


__all__ = ["SessionEventLoopError", "SessionLoopHandlers", "ordered_session_events",
           "run_forward_session"]
