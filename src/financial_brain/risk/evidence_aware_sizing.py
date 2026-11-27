"""Volatility-aware sizing shrunk by point-in-time calibration and forward evidence."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from math import isfinite, sqrt


class EvidenceAwareSizingError(ValueError):
    """Invalid sizing policy or evidence that cannot be used as of the decision."""


@dataclass(frozen=True, slots=True)
class EvidenceAwareSizingPolicy:
    policy_id: str
    frozen_at: datetime
    first_eligible_session: date
    target_volatility: float
    max_calibration_error: float
    min_calibration_observations: int
    max_calibration_age: timedelta
    min_recent_trades: int
    max_recent_evidence_age: timedelta
    min_recent_net_alpha_lcb: float
    max_position_fraction: float
    uncalibrated_multiplier: float = 0.05


@dataclass(frozen=True, slots=True)
class SizingEvidence:
    decision_at: datetime
    session: date
    requested_fraction: float
    realized_volatility: float
    calibration_error: float
    calibration_observations: int
    training_through: datetime
    validation_start: datetime
    validation_end: datetime
    calibration_artifact_sha256: str
    recent_net_alpha_lcb: float
    recent_trade_count: int
    recent_period_start: datetime
    recent_period_end: datetime
    recent_outcomes_sha256: str


@dataclass(frozen=True, slots=True)
class EvidenceAwareSizingDecision:
    requested_fraction: float
    final_fraction: float
    volatility_multiplier: float
    calibration_multiplier: float
    recent_evidence_multiplier: float
    policy_id: str
    decision_at: datetime
    calibration_artifact_sha256: str
    recent_outcomes_sha256: str
    reasons: tuple[str, ...]


def _aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def _sha256(value: str) -> bool:
    return re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _validate_policy(policy: EvidenceAwareSizingPolicy) -> None:
    if not policy.policy_id.strip() or not _aware(policy.frozen_at):
        raise EvidenceAwareSizingError("policy identity and aware freeze time are required")
    if policy.frozen_at.date() >= policy.first_eligible_session:
        raise EvidenceAwareSizingError("sizing policy must predate the eligible session")
    finite = (policy.target_volatility, policy.max_calibration_error,
              policy.min_recent_net_alpha_lcb, policy.max_position_fraction,
              policy.uncalibrated_multiplier)
    if not all(isfinite(value) for value in finite):
        raise EvidenceAwareSizingError("sizing policy values must be finite")
    if (policy.target_volatility <= 0 or not 0 < policy.max_calibration_error <= 1
            or type(policy.min_calibration_observations) is not int
            or policy.min_calibration_observations < 1
            or policy.max_calibration_age <= timedelta(0)
            or type(policy.min_recent_trades) is not int or policy.min_recent_trades < 1
            or policy.max_recent_evidence_age <= timedelta(0)
            or policy.min_recent_net_alpha_lcb <= 0
            or not 0 < policy.max_position_fraction <= 1
            or not 0 <= policy.uncalibrated_multiplier <= 0.1):
        raise EvidenceAwareSizingError("sizing policy limits are invalid")


def size_with_evidence(policy: EvidenceAwareSizingPolicy,
                       evidence: SizingEvidence) -> EvidenceAwareSizingDecision:
    """Apply hard volatility, calibration and recent-net-edge shrinkage to a requested fraction.

    Calibration uses a frozen out-of-sample validation window. Its evidence window and the
    recent net-outcome window must end before the decision timestamp. Input hashes identify
    the source artifacts; this function does not independently sign or authenticate them.
    """
    _validate_policy(policy)
    clocks = (evidence.decision_at, evidence.training_through, evidence.validation_start,
              evidence.validation_end, evidence.recent_period_start, evidence.recent_period_end)
    if not all(_aware(stamp) for stamp in clocks):
        raise EvidenceAwareSizingError("evidence timestamps must be timezone aware")
    if evidence.session < policy.first_eligible_session or evidence.decision_at.date() < policy.first_eligible_session:
        raise EvidenceAwareSizingError("decision predates policy eligibility")
    if policy.frozen_at >= evidence.decision_at:
        raise EvidenceAwareSizingError("policy must be frozen before the decision")
    if not evidence.training_through < evidence.validation_start <= evidence.validation_end < evidence.decision_at:
        raise EvidenceAwareSizingError("calibration window must be strictly out of sample and point in time")
    if not evidence.validation_end < evidence.recent_period_start <= evidence.recent_period_end < evidence.decision_at:
        raise EvidenceAwareSizingError("recent net outcomes must follow validation and predate the decision")
    if evidence.decision_at - evidence.validation_end > policy.max_calibration_age:
        raise EvidenceAwareSizingError("calibration evidence is stale")
    if evidence.decision_at - evidence.recent_period_end > policy.max_recent_evidence_age:
        raise EvidenceAwareSizingError("recent net-outcome evidence is stale")
    if (not _sha256(evidence.calibration_artifact_sha256)
            or not _sha256(evidence.recent_outcomes_sha256)):
        raise EvidenceAwareSizingError("calibration and forward-outcome artifact hashes are required")
    if (not isfinite(evidence.requested_fraction) or not 0 <= evidence.requested_fraction <= 1
            or not isfinite(evidence.realized_volatility) or evidence.realized_volatility <= 0
            or not isfinite(evidence.calibration_error) or not 0 <= evidence.calibration_error <= 1
            or type(evidence.calibration_observations) is not int or evidence.calibration_observations < 0
            or not isfinite(evidence.recent_net_alpha_lcb)
            or type(evidence.recent_trade_count) is not int or evidence.recent_trade_count < 0):
        raise EvidenceAwareSizingError("sizing observations are invalid")

    volatility_multiplier = min(1.0, policy.target_volatility / evidence.realized_volatility)
    calibration_qualified = (
        evidence.calibration_observations >= policy.min_calibration_observations
        and evidence.calibration_error <= policy.max_calibration_error
    )
    calibration_multiplier = (
        min(1.0, evidence.calibration_observations / (4 * policy.min_calibration_observations))
        * max(0.0, 1.0 - evidence.calibration_error / policy.max_calibration_error)
        if calibration_qualified else policy.uncalibrated_multiplier
    )
    recent_sample_multiplier = min(1.0, sqrt(evidence.recent_trade_count / policy.min_recent_trades))
    recent_alpha_multiplier = max(
        0.0, min(1.0, evidence.recent_net_alpha_lcb / policy.min_recent_net_alpha_lcb)
    )
    recent_multiplier = recent_sample_multiplier * recent_alpha_multiplier
    final = min(policy.max_position_fraction,
                evidence.requested_fraction * volatility_multiplier
                * calibration_multiplier * recent_multiplier)
    reasons = []
    if volatility_multiplier < 1:
        reasons.append("REALIZED_VOLATILITY_REDUCTION")
    if not calibration_qualified:
        reasons.append("UNCALIBRATED_PROBABILITY_SEVERE_SHRINKAGE")
    elif calibration_multiplier < 1:
        reasons.append("CALIBRATION_QUALITY_OR_SAMPLE_SHRINKAGE")
    if recent_multiplier < 1:
        reasons.append("RECENT_NET_EVIDENCE_SHRINKAGE")
    if final == 0:
        reasons.append("NO_SIZE_AFTER_EVIDENCE_SHRINKAGE")
    return EvidenceAwareSizingDecision(
        requested_fraction=evidence.requested_fraction,
        final_fraction=final,
        volatility_multiplier=volatility_multiplier,
        calibration_multiplier=calibration_multiplier,
        recent_evidence_multiplier=recent_multiplier,
        policy_id=policy.policy_id,
        decision_at=evidence.decision_at,
        calibration_artifact_sha256=evidence.calibration_artifact_sha256,
        recent_outcomes_sha256=evidence.recent_outcomes_sha256,
        reasons=tuple(reasons),
    )
