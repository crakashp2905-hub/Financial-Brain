from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.forward_edge.evidence_labels import classify
from financial_brain.forward_edge.market_data import delayed_fixture_replay
from financial_brain.forward_edge.market_events import EventKind, MarketEvent
from financial_brain.kernel.evidence import EvidenceClass

UTC = timezone.utc
AT = datetime(2026, 11, 23, 9, 15, tzinfo=UTC)


def _fixture(event_id: str, *, sequence: int) -> MarketEvent:
    return MarketEvent(
        event_id, EventKind.TICK, "NSE", "NSE:INFY", AT, AT, AT,
        {"price": 100 + sequence}, "SYNTHETIC_FIXTURE", sequence=sequence)


def test_delayed_fixture_replay_preserves_source_clock_and_hashes_synthetic_provenance() -> None:
    original = [_fixture("event-2", sequence=2), _fixture("event-1", sequence=1)]
    delay = timedelta(milliseconds=250)
    first = delayed_fixture_replay(original, delay=delay)
    second = delayed_fixture_replay(original, delay=delay)

    assert [row.sequence for row in first] == [1, 2]
    assert first == second
    assert all(row.source == "SYNTHETIC_FIXTURE_REPLAY" for row in first)
    assert all(row.event_id.startswith("fixture-replay:") for row in first)
    assert all(len(row.evidence_id or "") == 64 for row in first)
    assert all(row.exchange_at == original_row.exchange_at for row, original_row in zip(first, reversed(original)))
    assert all(row.received_at == AT + delay and row.available_at == AT + delay for row in first)
    assert classify("fixture_replay", market_observed=True, session_complete=True) is \
        EvidenceClass.DETERMINISTIC_FIXTURE


def test_delayed_fixture_replay_refuses_market_provider_events() -> None:
    market = MarketEvent("kite", EventKind.TICK, "NSE", "NSE:INFY", AT, AT, AT,
                         {"price": 100}, "KITE_CONNECT_READ_ONLY")
    with pytest.raises(ValueError, match="synthetic fixture events only"):
        delayed_fixture_replay([market], delay=timedelta(seconds=1))


def test_delayed_fixture_replay_rejects_negative_delay_and_duplicate_ids() -> None:
    event = _fixture("event", sequence=1)
    with pytest.raises(ValueError, match="non-negative"):
        delayed_fixture_replay([event], delay=timedelta(milliseconds=-1))
    with pytest.raises(ValueError, match="duplicate event IDs"):
        delayed_fixture_replay([event, event], delay=timedelta(0))
