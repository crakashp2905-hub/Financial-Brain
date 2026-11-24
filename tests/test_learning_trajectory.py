from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.learning.trajectory import (
    EpisodeKey,
    PropensityStatus,
    TrajectoryError,
    Transition,
    TransitionEvidence,
)

T0 = datetime(2026, 11, 24, 9, 15, tzinfo=timezone.utc)
H = "a" * 64


def episode() -> EpisodeKey:
    return EpisodeKey("dataset-v1", "NSE:INFY", "session-1", "momentum", "policy-v1", "paper-1", "ep-1")


def row(**changes) -> Transition:
    body = {
        "episode": episode(), "decision_id": "decision-1",
        "action_decision_ts": T0, "first_eligible_fill_ts": T0 + timedelta(seconds=1),
        "state": {"mid": 100.0, "nested": {"vol": 0.2}},
        "eligible_actions": ("NO_TRADE", "LONG"), "chosen_action": "LONG",
        "propensity_status": PropensityStatus.UNAVAILABLE, "action_probability": None,
        "outcome_status": "PENDING", "fill": None, "reward": None, "next_state": None,
        "done": False, "evidence": TransitionEvidence.LOCAL_PAPER,
        "provenance_sha256": H, "source_event_sha256": "b" * 64,
    }
    return Transition(**(body | changes))


def test_transition_hash_is_stable_and_nested_state_is_immutable():
    source_state = {"mid": 100.0, "nested": {"vol": 0.2}}
    first = row(state=source_state)
    source_state["mid"] = 999.0
    assert first.state["mid"] == 100.0
    with pytest.raises(TypeError):
        first.state["mid"] = 101.0
    with pytest.raises(TypeError):
        first.state["nested"]["vol"] = 0.4
    assert first.row_sha256 == row().row_sha256


def test_missing_propensity_is_explicit_and_never_fabricated():
    with pytest.raises(TrajectoryError, match="fabricated probability"):
        row(action_probability=0.5)
    assert row().propensity_status is PropensityStatus.UNAVAILABLE


def test_known_propensity_requires_probability_in_unit_interval():
    assert row(propensity_status=PropensityStatus.KNOWN, action_probability=0.25).action_probability == 0.25
    with pytest.raises(TrajectoryError, match=r"in \(0, 1\]"):
        row(propensity_status=PropensityStatus.KNOWN, action_probability=0)


def test_transition_rejects_fill_before_decision_and_unsupported_action():
    with pytest.raises(TrajectoryError, match="cannot precede"):
        row(first_eligible_fill_ts=T0 - timedelta(seconds=1))
    with pytest.raises(TrajectoryError, match="outside behavior-policy support"):
        row(chosen_action="SHORT")


def test_replay_and_fixtures_cannot_be_claimed_as_observed_fills():
    for evidence in (TransitionEvidence.HISTORICAL_REPLAY, TransitionEvidence.DETERMINISTIC_FIXTURE):
        with pytest.raises(TrajectoryError, match="cannot be labeled observed"):
            row(evidence=evidence, outcome_status="OBSERVED_FILL", fill={"quantity": 1})


def test_no_trade_and_rejected_opportunities_are_representable_with_reasons():
    no_trade = row(chosen_action="NO_TRADE", outcome_status="NO_TRADE", rejected_reason_codes=("STALE_QUOTE",),
                   reward=0.0, next_state={"mid": 100.0}, done=False)
    assert no_trade.chosen_action == "NO_TRADE"
    with pytest.raises(TrajectoryError, match="reason codes"):
        row(outcome_status="REJECTED", rejected_reason_codes=())


def test_row_hash_mismatch_is_rejected():
    value = row()
    with pytest.raises(TrajectoryError, match="does not match"):
        replace(value, row_sha256="c" * 64)
