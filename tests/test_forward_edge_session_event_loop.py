from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
from typing import Any
from typing import cast

import pytest

from financial_brain.forward_edge.market_events import EventKind, MarketEvent
from financial_brain.forward_edge.session_event_loop import (
    SessionEventLoopError,
    SessionLoopHandlers,
    ordered_session_events,
    run_forward_session,
)
from financial_brain.forward_edge.session_state import ForwardRunConfig, SessionState
from financial_brain.forward_edge.session_runner import ForwardSessionRunner
from financial_brain.forward_edge.session_state import ForwardSessionStore
from financial_brain.forward_edge.session_ops import REQUIRED_PREFLIGHT
from financial_brain.forward_edge.venue_calendar import Session, VenueCalendar

T0 = datetime(2026, 11, 23, 4, 0, tzinfo=timezone.utc)


def _event(event_id: str, kind: EventKind, available: datetime, payload: dict[str, Any], *, sequence: int = 1):
    return MarketEvent(
        event_id=event_id, kind=kind, venue="NSE", instrument_id="NSE:INFY",
        event_time=available, received_at=available, available_at=available,
        payload=payload, source="SYNTHETIC_FIXTURE", sequence=sequence,
        evidence_id="a" * 64,
    )


class _Runner:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    def plan_calendar_day(self, session_id: str, config: Any, **kwargs: Any) -> SessionState:
        self.calls.append(("open", session_id))
        return SessionState.OPEN

    def ingest(self, session_id: str, config: Any, events: list[MarketEvent]) -> dict[str, int]:
        self.calls.append(("ingest", events[0].event_id))
        return {"received": 1, "persisted": 1, "duplicate": 0}

    def close_session(self, session_id: str, *, complete: bool, now: datetime,
                      reason: str | None = None) -> dict[str, Any]:
        self.calls.append(("close", complete, reason))
        return {"status": "CLOSED" if complete else "INCOMPLETE"}


def _handlers(calls: list[tuple[str, str]]) -> SessionLoopHandlers:
    def close(session_id: str, at: datetime) -> bool:
        calls.append(("on_close", session_id))
        return True

    return SessionLoopHandlers(
        on_open=lambda session_id: calls.append(("on_open", session_id)),
        on_market_update=lambda event: calls.append(("market", event.event_id)),
        on_completed_bar=lambda event: calls.append(("bar", event.event_id)),
        on_corporate_action=lambda event: calls.append(("corporate_action", event.event_id)),
        on_margin_change=lambda event: calls.append(("margin", event.event_id)),
        on_auxiliary_event=lambda event: calls.append(("auxiliary", event.event_id)),
        on_close=close,
    )


def _events() -> list[MarketEvent]:
    same_time = T0 + timedelta(minutes=1)
    return [
        _event("bar-partial", EventKind.BAR, same_time,
               {"open": 100, "high": 101, "low": 99, "close": 100,
                "volume": 20, "is_complete": False}, sequence=2),
        _event("quote-1", EventKind.QUOTE, same_time,
               {"bid": 99.95, "ask": 100.05}, sequence=3),
        _event("margin-1", EventKind.MARGIN_CHANGE, same_time,
               {"change_id": "m1", "margin_before_inr": 1000,
                "margin_after_inr": 1200, "currency": "INR"}, sequence=1),
        _event("action-1", EventKind.CORPORATE_ACTION, same_time,
               {"action_id": "ca1", "action_type": "DIVIDEND"}, sequence=4),
        _event("bar-complete", EventKind.BAR, same_time,
               {"open": 100, "high": 101, "low": 99, "close": 100,
                "volume": 20, "is_complete": True}, sequence=5),
    ]


def _run(events: list[MarketEvent]) -> tuple[dict[str, object], list[tuple[str, str]], _Runner]:
    runner = _Runner()
    callbacks: list[tuple[str, str]] = []
    result = _run_with_runner(runner, events, _handlers(callbacks))
    return result, callbacks, runner


def _run_with_runner(runner: _Runner, events: list[MarketEvent],
                     handlers: SessionLoopHandlers) -> dict[str, object]:
    calendar = VenueCalendar((Session(
        "NSE", date(2026, 11, 23), time(9, 15), time(15, 30), "NORMAL",
        "test-session-calendar", "c" * 64, T0,
    ),), source_as_of=date(2026, 11, 23))
    config = ForwardRunConfig(
        date(2026, 11, 23), "fixture", "d" * 64, "NIFTY50_TRI", ("NSE:INFY",),
        Decimal("100000"), {}, "ledger",
    )
    result = run_forward_session(
        cast(ForwardSessionRunner, runner), session_id="s1",
        config=config, venue="NSE", calendar=calendar,
        opened_at=T0, closed_at=T0 + timedelta(minutes=2), preflight={"clock": True},
        events=events, handlers=handlers,
    )
    return result


def test_session_loop_orders_all_events_and_runs_exit_monitor_before_bar_strategy() -> None:
    result, callbacks, runner = _run(_events())
    assert [value for kind, value in callbacks if kind in {"corporate_action", "margin", "market", "bar"}] == [
        "action-1", "margin-1", "quote-1", "bar-partial", "bar-complete", "bar-complete",
    ]
    assert callbacks.index(("market", "quote-1")) < callbacks.index(("bar", "bar-complete"))
    assert ("bar", "bar-partial") not in callbacks
    assert result["status"] == "CLOSED"
    assert result["events_processed"] == 5
    assert result["completed_bars_dispatched"] == 1
    assert [row[1] for row in runner.calls if row[0] == "ingest"] == [
        "action-1", "margin-1", "quote-1", "bar-partial", "bar-complete",
    ]


def test_event_loop_order_and_outputs_are_independent_of_input_batch_order() -> None:
    events = _events()
    assert [x.event_id for x in ordered_session_events(events)] == [
        x.event_id for x in ordered_session_events(list(reversed(events)))
    ]
    assert _run(events)[:2] == _run(list(reversed(events)))[:2]


def test_event_loop_rejects_duplicate_ids_and_close_before_event_availability() -> None:
    event = _events()[0]
    with pytest.raises(SessionEventLoopError, match="duplicate event IDs"):
        ordered_session_events([event, event])
    with pytest.raises(SessionEventLoopError, match="close precedes"):
        _run([_event("late", EventKind.QUOTE, T0 + timedelta(minutes=3),
                     {"bid": 99.95, "ask": 100.05})])


def test_handler_failure_forces_incomplete_session_close() -> None:
    runner = _Runner()
    handlers = _handlers([])

    def fail(event: MarketEvent) -> None:
        raise RuntimeError("handler failure")

    handlers = SessionLoopHandlers(
        on_open=handlers.on_open, on_market_update=fail,
        on_completed_bar=handlers.on_completed_bar,
        on_corporate_action=handlers.on_corporate_action,
        on_margin_change=handlers.on_margin_change,
        on_auxiliary_event=handlers.on_auxiliary_event,
        on_close=handlers.on_close,
    )
    with pytest.raises(RuntimeError, match="handler failure"):
        _run_with_runner(runner, [_events()[1]], handlers)
    assert ("close", False, "deterministic event loop failed: RuntimeError") in runner.calls


def test_margin_change_requires_explicit_nonnegative_inr_values() -> None:
    with pytest.raises(ValueError, match="margin change currency"):
        _event("bad-margin", EventKind.MARGIN_CHANGE, T0,
               {"change_id": "m1", "margin_before_inr": 1,
                "margin_after_inr": 2, "currency": "USD"})


def test_event_loop_drives_real_runner_open_ingest_and_incomplete_close(tmp_path: Path) -> None:
    permissions = tmp_path / "permissions.json"
    permissions.write_text(json.dumps({"schema": "fb.data_permissions.v1", "permissions": []}),
                           encoding="utf-8")
    runner = ForwardSessionRunner(
        ForwardSessionStore(tmp_path / "state.sqlite"), tmp_path / "journal.sqlite",
        permission_registry_path=permissions,
    )
    day = date(2026, 11, 23)
    config = ForwardRunConfig(
        day, "fixture", "a" * 64, "NIFTY50_TRI", ("NSE:INFY",), Decimal("100000"),
        {"allow_synthetic_fixture_events": True}, "ledger",
    )
    calendar = VenueCalendar((Session(
        "NSE", day, time(9, 15), time(15, 30), "NORMAL", "fixture-session-calendar",
        "b" * 64, T0,
    ),), source_as_of=day)
    dispatched: list[str] = []
    handlers = SessionLoopHandlers(
        on_open=lambda session_id: dispatched.append("opened"),
        on_market_update=lambda event: dispatched.append(event.event_id),
        on_completed_bar=lambda event: pytest.fail("quote-only session dispatched a bar"),
        on_corporate_action=lambda event: pytest.fail("unexpected corporate action"),
        on_margin_change=lambda event: pytest.fail("unexpected margin event"),
        on_auxiliary_event=lambda event: pytest.fail("unexpected auxiliary event"),
        on_close=lambda session_id, at: False,
    )
    quote = _event("loop-quote", EventKind.QUOTE, T0, {"bid": 99.95, "ask": 100.05})
    try:
        result = run_forward_session(
            runner, session_id="loop-real", config=config, venue="NSE", calendar=calendar,
            opened_at=T0, closed_at=T0 + timedelta(seconds=1),
            preflight={name: True for name in REQUIRED_PREFLIGHT}, events=[quote], handlers=handlers,
        )
        assert result["status"] == "INCOMPLETE"
        assert dispatched == ["opened", "loop-quote"]
        assert [row["event_id"] for row in runner.records("loop-real")["events"]] == ["loop-quote"]
    finally:
        runner.close()
