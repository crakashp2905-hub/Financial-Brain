from datetime import date, datetime, timedelta, timezone

import pytest

from financial_brain.risk.evidence_aware_sizing import (
    EvidenceAwareSizingError,
    EvidenceAwareSizingPolicy,
    SizingEvidence,
    size_with_evidence,
)


UTC = timezone.utc
NOW = datetime(2026, 11, 27, 10, tzinfo=UTC)


def policy(**updates):
    values = dict(
        policy_id="sizing-v1",
        frozen_at=datetime(2026, 11, 20, tzinfo=UTC),
        first_eligible_session=date(2026, 11, 21),
        target_volatility=0.20,
        max_calibration_error=0.05,
        min_calibration_observations=100,
        max_calibration_age=timedelta(days=30),
        min_recent_trades=25,
        max_recent_evidence_age=timedelta(days=5),
        min_recent_net_alpha_lcb=0.01,
        max_position_fraction=0.25,
    )
    values.update(updates)
    return EvidenceAwareSizingPolicy(**values)


def evidence(**updates):
    values = dict(
        decision_at=NOW,
        session=date(2026, 11, 27),
        requested_fraction=0.20,
        realized_volatility=0.20,
        calibration_error=0.01,
        calibration_observations=400,
        training_through=datetime(2026, 11, 14, tzinfo=UTC),
        validation_start=datetime(2026, 11, 15, tzinfo=UTC),
        validation_end=datetime(2026, 11, 18, tzinfo=UTC),
        calibration_artifact_sha256="a" * 64,
        recent_net_alpha_lcb=0.01,
        recent_trade_count=25,
        recent_period_start=datetime(2026, 11, 19, tzinfo=UTC),
        recent_period_end=datetime(2026, 11, 25, tzinfo=UTC),
        recent_outcomes_sha256="b" * 64,
    )
    values.update(updates)
    return SizingEvidence(**values)


def test_qualified_calibration_recent_net_edge_and_volatility_bound_size():
    result = size_with_evidence(policy(), evidence(realized_volatility=0.40))
    assert result.volatility_multiplier == pytest.approx(0.5)
    assert result.calibration_multiplier == pytest.approx(0.8)
    assert result.recent_evidence_multiplier == pytest.approx(1.0)
    assert result.final_fraction == pytest.approx(0.08)
    assert result.calibration_artifact_sha256 == "a" * 64
    assert result.recent_outcomes_sha256 == "b" * 64
    assert "REALIZED_VOLATILITY_REDUCTION" in result.reasons


def test_uncalibrated_probability_gets_severe_shrinkage():
    result = size_with_evidence(policy(), evidence(calibration_observations=20))
    assert result.calibration_multiplier == 0.05
    assert result.final_fraction == pytest.approx(0.01)
    assert "UNCALIBRATED_PROBABILITY_SEVERE_SHRINKAGE" in result.reasons


def test_recent_negative_or_weak_net_edge_moves_size_to_zero_or_down():
    no_edge = size_with_evidence(policy(), evidence(recent_net_alpha_lcb=0.0))
    weak_edge = size_with_evidence(policy(), evidence(recent_net_alpha_lcb=0.0025))
    assert no_edge.final_fraction == 0
    assert "NO_SIZE_AFTER_EVIDENCE_SHRINKAGE" in no_edge.reasons
    assert weak_edge.final_fraction == pytest.approx(0.04)


def test_position_cap_applies_after_all_sizing_multipliers():
    result = size_with_evidence(
        policy(max_position_fraction=0.05),
        evidence(requested_fraction=1.0),
    )
    assert result.final_fraction == 0.05


@pytest.mark.parametrize(
    "changes",
    [
        {"decision_at": datetime(2026, 11, 27, 10)},
        {"validation_end": NOW},
        {"recent_period_start": datetime(2026, 11, 18, tzinfo=UTC)},
        {"recent_period_end": datetime(2026, 11, 20, tzinfo=UTC)},
        {
            "validation_start": datetime(2026, 10, 15, tzinfo=UTC),
            "validation_end": datetime(2026, 10, 18, tzinfo=UTC),
            "recent_period_start": datetime(2026, 10, 19, tzinfo=UTC),
        },
        {"calibration_artifact_sha256": "missing"},
        {"recent_net_alpha_lcb": float("nan")},
        {"realized_volatility": 0.0},
    ],
)
def test_missing_future_or_invalid_sizing_evidence_fails_closed(changes):
    with pytest.raises(EvidenceAwareSizingError):
        size_with_evidence(policy(), evidence(**changes))


def test_policy_cannot_be_used_for_a_session_before_it_was_frozen():
    with pytest.raises(EvidenceAwareSizingError, match="eligible session"):
        size_with_evidence(policy(first_eligible_session=date(2026, 11, 20)), evidence())

