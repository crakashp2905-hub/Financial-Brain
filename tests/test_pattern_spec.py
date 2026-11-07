"""Pattern contract tests (CP-0027..CP-0034, CP-0038, CP-0040)."""

from __future__ import annotations

import math

import pytest

from financial_brain.patterns.spec import (
    EventState,
    EvidenceTier,
    InvalidContract,
    InvalidTransition,
    PatternEvent,
    PatternSpec,
    canonical_json,
    make_event_id,
    transition,
)

SPEC_KW = dict(
    pattern_id="B01",
    version="1",
    timeframe="1D",
    parameters={"left": 2, "right": 2, "ratio": 0.5},
    recognition_rule="causal pivot",
    confirmation_rule="close beyond level",
    invalidation_rule="close back inside",
    source_id="bulkowski",
)


def _event(**overrides) -> PatternEvent:
    kw = dict(
        event_id="pev_test",
        spec_sha256="0" * 64,
        lineage="ISIN_TEST",
        timeframe="1D",
        candidate_known_at_utc="2026-01-05T10:00:00+00:00",
        status=EventState.CANDIDATE,
        data_sha256="1" * 64,
    )
    kw.update(overrides)
    return PatternEvent(**kw)


def test_spec_hash_is_stable_and_order_independent() -> None:
    a = PatternSpec(**SPEC_KW)
    b = PatternSpec(**{**SPEC_KW, "parameters": {"ratio": 0.5, "right": 2, "left": 2}})
    assert a.canonical_bytes() == b.canonical_bytes()
    assert a.sha256 == b.sha256
    assert len(a.sha256) == 64


def test_spec_parameters_are_frozen_copies() -> None:
    params = {"left": 2}
    spec = PatternSpec(**{**SPEC_KW, "parameters": params})
    params["left"] = 99
    assert spec.parameters == {"left": 2}


def test_spec_round_trip_preserves_identity() -> None:
    spec = PatternSpec(**SPEC_KW)
    assert PatternSpec.from_dict(spec.to_dict()) == spec
    assert PatternSpec.from_dict(spec.to_dict()).sha256 == spec.sha256


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf")])
def test_non_finite_parameters_fail_closed(bad: float) -> None:
    with pytest.raises(InvalidContract):
        PatternSpec(**{**SPEC_KW, "parameters": {"x": bad}})


def test_naive_timestamp_rejected() -> None:
    with pytest.raises(InvalidContract, match="timezone-aware"):
        _event(candidate_known_at_utc="2026-01-05T10:00:00")


def test_confirmation_cannot_precede_candidate_knowledge() -> None:
    with pytest.raises(InvalidContract, match="precedes"):
        _event(
            status=EventState.CONFIRMED,
            confirmed_at_utc="2026-01-04T10:00:00+00:00",
        )


def test_entry_cannot_precede_confirmation() -> None:
    with pytest.raises(InvalidContract, match="entry cannot precede"):
        _event(
            status=EventState.CONFIRMED,
            confirmed_at_utc="2026-01-06T10:00:00+00:00",
            earliest_entry_at_utc="2026-01-05T11:00:00+00:00",
        )


def test_confirmed_requires_confirmation_time() -> None:
    with pytest.raises(InvalidContract):
        _event(status=EventState.CONFIRMED)


def test_detector_cannot_emit_non_descriptive_evidence() -> None:
    with pytest.raises(InvalidContract, match="DESCRIPTIVE"):
        _event(evidence_tier=EvidenceTier.EXTERNALLY_CONFIRMED)


def test_geometry_rejects_nan() -> None:
    with pytest.raises(InvalidContract):
        _event(geometry={"level": math.nan})


def test_legal_lifecycle_and_immutability() -> None:
    cand = _event()
    conf = transition(
        cand, EventState.CONFIRMED, confirmed_at_utc="2026-01-06T10:00:00+00:00"
    )
    assert conf.status is EventState.CONFIRMED
    assert cand.status is EventState.CANDIDATE  # original untouched
    inv = transition(
        conf, EventState.INVALIDATED, invalidated_at_utc="2026-01-07T10:00:00+00:00"
    )
    assert inv.status is EventState.INVALIDATED


@pytest.mark.parametrize(
    ("src", "dst"),
    [
        (EventState.INVALIDATED, EventState.CONFIRMED),
        (EventState.EXPIRED, EventState.CANDIDATE),
        (EventState.SUPERSEDED, EventState.EXPIRED),
        (EventState.CONFIRMED, EventState.CANDIDATE),
    ],
)
def test_illegal_transitions_raise(src: EventState, dst: EventState) -> None:
    event = _event(status=src) if src is EventState.CANDIDATE else None
    if event is None:
        base = _event()
        if src is EventState.CONFIRMED:
            base = transition(base, EventState.CONFIRMED, confirmed_at_utc="2026-01-06T10:00:00+00:00")
        elif src in (EventState.INVALIDATED, EventState.EXPIRED, EventState.SUPERSEDED):
            extra = {"invalidated_at_utc": "2026-01-06T10:00:00+00:00"} if src is EventState.INVALIDATED else {}
            base = transition(base, src, **extra)
        event = base
    with pytest.raises(InvalidTransition):
        transition(event, dst, confirmed_at_utc="2026-01-06T10:00:00+00:00")


def test_event_id_is_stable_and_sensitive_to_inputs() -> None:
    kw = dict(spec_sha256="a" * 64, lineage="L", timeframe="1D", candidate_known_at_utc="2026-01-05T10:00:00+00:00")
    assert make_event_id(**kw) == make_event_id(**kw)
    assert make_event_id(**kw).startswith("pev_")
    assert make_event_id(**{**kw, "timeframe": "1W"}) != make_event_id(**kw)


def test_event_round_trip_and_canonical_bytes() -> None:
    event = _event(geometry={"pivots": [1, 2], "level": 101.5})
    assert PatternEvent.from_dict(event.to_dict()) == event
    assert canonical_json(event.to_dict()) == canonical_json(PatternEvent.from_dict(event.to_dict()).to_dict())
