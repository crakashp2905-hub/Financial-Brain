from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from financial_brain.kernel.instrument_history import (
    IdentityEventKind,
    InstrumentHistory,
    InstrumentHistoryError,
    InstrumentIdentityEvent,
)

HASH = "b" * 64
PUBLISHED = datetime(2026, 11, 20, 10, tzinfo=timezone.utc)


def _event(kind: IdentityEventKind = IdentityEventKind.TICKER_CHANGE,
           destinations: tuple[str, ...] = ("NSE:NEW",)) -> InstrumentIdentityEvent:
    return InstrumentIdentityEvent(
        event_id="action-1", kind=kind, from_id="NSE:OLD", to_ids=destinations,
        effective_at=date(2026, 11, 19), published_at=PUBLISHED,
        recorded_at=PUBLISHED + timedelta(minutes=2), source_ref="exchange-notice-1",
        source_sha256=HASH, details="Explicit source-backed identity transition",
    )


def test_ticker_transition_is_visible_only_after_publication_and_keeps_old_id() -> None:
    event = _event()
    history = InstrumentHistory((event,))
    assert history.known_for("NSE:OLD", as_of=PUBLISHED - timedelta(seconds=1)) == ()
    assert history.known_for("NSE:OLD", as_of=PUBLISHED) == (event,)
    assert history.announced_successors("NSE:OLD", as_of=PUBLISHED) == ("NSE:NEW",)
    assert event.from_id == "NSE:OLD" and event.to_ids == ("NSE:NEW",)
    assert len(event.sha256()) == 64


def test_scheme_merger_can_link_multiple_successor_schemes() -> None:
    event = _event(IdentityEventKind.SCHEME_MERGER, ("AMFI:SCHEME-B", "AMFI:SCHEME-C"))
    history = InstrumentHistory((event,))
    assert history.announced_successors("NSE:OLD", as_of=PUBLISHED) == (
        "AMFI:SCHEME-B", "AMFI:SCHEME-C")


def test_identity_transition_conflicts_and_missing_destinations_fail_closed() -> None:
    history = InstrumentHistory((_event(),))
    history.append(_event())  # exact replay is idempotent
    with pytest.raises(InstrumentHistoryError, match="conflicting"):
        history.append(_event(destinations=("NSE:OTHER",)))
    with pytest.raises(InstrumentHistoryError, match="requires at least one"):
        _event(destinations=())


def test_future_publication_and_naive_query_time_are_not_looked_ahead() -> None:
    with pytest.raises(InstrumentHistoryError, match="recorded before publication"):
        InstrumentIdentityEvent(
            "bad", IdentityEventKind.ISIN_CHANGE, "old", ("new",), date(2026, 1, 1),
            PUBLISHED, PUBLISHED - timedelta(seconds=1), "ref", HASH)
    with pytest.raises(InstrumentHistoryError, match="timezone aware"):
        InstrumentHistory((_event(),)).known_for("NSE:OLD", as_of=datetime(2026, 11, 21))
