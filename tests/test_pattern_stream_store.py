"""Streaming pivots, event log, decision clock, signal, provenance and promotion (wave 2)."""

from __future__ import annotations

import dataclasses
import random

import pytest

from financial_brain.patterns.catalogue import (
    CatalogueEntry,
    PromotionRefused,
    TypedStat,
    promote_definition_status,
)
from financial_brain.patterns.events_store import (
    ConflictingVersion,
    EventLog,
    sorted_events,
    version_known_at,
)
from financial_brain.patterns.pivots import causal_pivots
from financial_brain.patterns.spec import (
    EventState,
    InvalidContract,
    InvalidTransition,
    PatternEvent,
    PatternSignal,
    Provenance,
    transition,
)
from financial_brain.patterns.streaming import StreamingPivots

T0 = "2026-01-05T10:00:00+00:00"
T1 = "2026-01-06T10:00:00+00:00"
T2 = "2026-01-07T10:00:00+00:00"


def _series(seed: int, n: int = 80) -> tuple[list[float], list[float]]:
    rng = random.Random(seed)
    closes = [100.0]
    for _ in range(n - 1):
        closes.append(max(1.0, closes[-1] + rng.gauss(0, 1.5)))
    return [c + rng.random() for c in closes], [c - rng.random() for c in closes]


@pytest.mark.parametrize("seed", range(25))
def test_streaming_equals_batch_on_every_prefix(seed: int) -> None:
    highs, lows = _series(seed)
    full = causal_pivots(highs, lows, left=2, right=3)
    stream = StreamingPivots(left=2, right=3)
    for t, (h, lo) in enumerate(zip(highs, lows, strict=True)):
        emitted = stream.push(h, lo)
        expected = [p for p in full if p.known_at_index == t]
        assert emitted == expected


def test_streaming_memory_is_bounded() -> None:
    stream = StreamingPivots(left=3, right=2)
    for i in range(10_000):
        stream.push(float(i % 17), float(i % 13))
    assert stream.buffered_bars == 3 + 2 + 1


def test_streaming_rejects_non_finite_bars() -> None:
    stream = StreamingPivots()
    with pytest.raises(ValueError):
        stream.push(float("nan"), 1.0)


def _candidate(event_id: str = "pev_a", lineage: str = "L1") -> PatternEvent:
    return PatternEvent(
        event_id=event_id,
        spec_sha256="0" * 64,
        lineage=lineage,
        timeframe="1D",
        candidate_known_at_utc=T0,
        status=EventState.CANDIDATE,
        data_sha256="1" * 64,
    )


def test_log_is_append_only_and_idempotent() -> None:
    log = EventLog()
    cand = _candidate()
    assert log.append(cand) is True
    assert log.append(cand) is False  # identical duplicate: no-op
    assert len(log) == 1


def test_conflicting_payload_under_same_version_fails_closed() -> None:
    log = EventLog()
    log.append(_candidate())
    with pytest.raises(ConflictingVersion):
        log.append(dataclasses.replace(_candidate(), data_sha256="2" * 64))


def test_first_version_must_be_candidate_and_transitions_enforced() -> None:
    log = EventLog()
    conf = transition(_candidate(), EventState.CONFIRMED, confirmed_at_utc=T1)
    with pytest.raises(InvalidContract):
        log.append(conf)
    log.append(_candidate())
    log.append(conf)
    inv = transition(conf, EventState.INVALIDATED, invalidated_at_utc=T2, invalidation_reason="CLOSE_BEYOND_FAILURE")
    log.append(inv)
    # A fresh event that is invalidated cannot later be confirmed: illegal transition.
    log2 = EventLog()
    log2.append(_candidate("pev_z"))
    invalid_z = transition(_candidate("pev_z"), EventState.INVALIDATED, invalidated_at_utc=T1, invalidation_reason="CLOSE_BEYOND_FAILURE")
    log2.append(invalid_z)
    with pytest.raises(InvalidTransition):
        log2.append(transition(invalid_z, EventState.CONFIRMED, confirmed_at_utc=T2))


def test_as_of_replays_only_what_was_knowable() -> None:
    log = EventLog()
    cand = _candidate()
    conf = transition(cand, EventState.CONFIRMED, confirmed_at_utc=T1)
    log.append(cand)
    log.append(conf)
    before = log.as_of("2026-01-05T12:00:00+00:00")
    assert [e.status for e in before] == [EventState.CANDIDATE]
    after = log.as_of(T2)
    assert [e.status for e in after] == [EventState.CONFIRMED]
    # history is never rewritten
    assert [e.status for e in log.history("pev_a")] == [EventState.CANDIDATE, EventState.CONFIRMED]


def test_unmodelled_states_are_rejected_not_guessed() -> None:
    log = EventLog()
    log.append(_candidate())
    with pytest.raises(InvalidContract):
        log.append(transition(_candidate(), EventState.EXPIRED))


def test_canonical_order_is_input_order_independent() -> None:
    events = [_candidate("pev_b", "L2"), _candidate("pev_a", "L1"), _candidate("pev_c", "L1")]
    forward = [e.event_id for e in sorted_events(events)]
    backward = [e.event_id for e in sorted_events(list(reversed(events)))]
    assert forward == backward == ["pev_a", "pev_c", "pev_b"]


def test_decision_clock_must_sit_between_confirmation_and_entry() -> None:
    conf = transition(_candidate(), EventState.CONFIRMED, confirmed_at_utc=T1)
    ok = dataclasses.replace(conf, decision_at_utc=T1, earliest_entry_at_utc=T2)
    assert ok.decision_at_utc == T1
    with pytest.raises(InvalidContract):
        dataclasses.replace(conf, decision_at_utc=T0.replace("05", "04"))
    with pytest.raises(InvalidContract, match="cannot precede confirmation"):
        dataclasses.replace(conf, decision_at_utc="2026-01-06T09:00:00+00:00")


def test_signal_is_research_only_and_has_no_order_fields() -> None:
    sig = PatternSignal(event_id="pev_a", label="double bottom tentative")
    assert not hasattr(sig, "side") and not hasattr(sig, "quantity")
    with pytest.raises(InvalidContract):
        PatternSignal(event_id="pev_a", label="x", research_only=False)  # type: ignore[arg-type]


def test_provenance_hash_changes_with_config() -> None:
    a = Provenance.config_hash({"left": 2})
    b = Provenance.config_hash({"left": 3})
    assert a != b
    p = Provenance("c" * 64, "7244f18", "NSE-cal-fixture", a)
    assert p.sha256() == Provenance(**p.to_dict()).sha256()


def _entry() -> CatalogueEntry:
    return CatalogueEntry(
        id="B01", source="bulkowski", name="x", family_raw="reversal", family="reversal",
        direction_raw="up", direction_class="bullish", definition_status="descriptive",
        sample_total=77, stats={"n": TypedStat(77, "77")}, basis=None, raw={},
    )


def test_status_cannot_be_raised_without_evidence() -> None:
    with pytest.raises(PromotionRefused, match="evidence"):
        promote_definition_status(_entry(), "formalized", None)
    promoted = promote_definition_status(_entry(), "formalized", "docs/sources/x.md#spec")
    assert promoted.definition_status == "formalized"


def test_status_cannot_skip_steps_or_demote() -> None:
    with pytest.raises(PromotionRefused, match="one step"):
        promote_definition_status(_entry(), "implemented", "ev")
    formal = promote_definition_status(_entry(), "formalized", "ev")
    with pytest.raises(PromotionRefused, match="demote"):
        promote_definition_status(formal, "descriptive", "ev")


def test_version_known_at_for_each_modelled_state() -> None:
    cand = _candidate()
    assert version_known_at(cand).isoformat() == "2026-01-05T10:00:00+00:00"


def test_one_bar_and_empty_inputs_are_stable() -> None:
    assert causal_pivots([], [], left=2, right=2) == []
    assert causal_pivots([5.0], [4.0], left=2, right=2) == []
    stream = StreamingPivots(left=2, right=2)
    assert stream.push(5.0, 4.0) == []
