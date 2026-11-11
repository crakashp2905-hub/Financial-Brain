from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.kernel.events import Backpressure, Event, EventKernel, snapshot_checksum

T0 = datetime(2026, 11, 9, 3, 45, tzinfo=timezone.utc)


def _ev(seconds: int, seq: int, kind: str = "bar") -> Event:
    return Event(T0 + timedelta(seconds=seconds), seq, kind)


def test_replay_is_byte_identical_regardless_of_submission_order() -> None:
    events = [_ev(2, 1), _ev(1, 0), _ev(1, 2), _ev(3, 0)]
    a, b = EventKernel(), EventKernel()
    for e in events:
        a.submit(e)
    for e in reversed(events):
        b.submit(e)
    a.drain()
    b.drain()
    assert snapshot_checksum(a.journal) == snapshot_checksum(b.journal)


def test_late_event_is_quarantined_not_applied() -> None:
    k = EventKernel(lateness=timedelta(seconds=5))
    k.submit(_ev(100, 0))
    k.drain()
    k.submit(_ev(10, 1, "late-quote"))
    k.drain()
    assert [e.kind for e in k.late] == ["late-quote"]
    assert all(e.kind != "late-quote" for e in k.journal)


def test_bounded_queue_refuses_overflow() -> None:
    k = EventKernel(max_queue=2)
    k.submit(_ev(1, 0))
    k.submit(_ev(2, 1))
    with pytest.raises(Backpressure):
        k.submit(_ev(3, 2))


def test_side_effect_is_dispatched_at_most_once() -> None:
    k = EventKernel()
    sent: list[str] = []
    assert k.side_effect("order-1", lambda: sent.append("x")) is True
    assert k.side_effect("order-1", lambda: sent.append("x")) is False
    assert sent == ["x"]


def test_crash_and_resume_matches_uninterrupted_run() -> None:
    events = [_ev(i, i) for i in range(6)]
    uninterrupted = EventKernel()
    for e in events:
        uninterrupted.submit(e)
    uninterrupted.drain()

    first = EventKernel()
    for e in events[:3]:
        first.submit(e)
    first.drain()
    cp = first.checkpoint()
    first.side_effect("order-1", lambda: None)
    cp = first.checkpoint()

    resumed = EventKernel()
    resumed.resume(cp, events)
    assert snapshot_checksum(resumed.journal) == snapshot_checksum(uninterrupted.journal)
    assert resumed.side_effect("order-1", lambda: None) is False


def test_checksum_detects_tampering() -> None:
    k = EventKernel()
    k.submit(Event(T0, 0, "fill", (("qty", 10),)))
    k.drain()
    original = snapshot_checksum(k.journal)
    tampered = [Event(T0, 0, "fill", (("qty", 11),))]
    assert snapshot_checksum(tampered) != original
