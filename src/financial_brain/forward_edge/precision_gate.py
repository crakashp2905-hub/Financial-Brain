"""Preregistered precision/sample-size stopping gate for prospective cohorts (FE-0097)."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date, datetime, timezone


class PrecisionGateError(ValueError):
    """Invalid stopping plan or evidence window."""


@dataclass(frozen=True)
class PrecisionPlan:
    plan_id: str
    preregistered_at: datetime
    first_eligible_session: date
    minimum_eligible_sessions: int
    maximum_eligible_sessions: int
    target_ci_half_width: float
    confidence_level: float

    def __post_init__(self) -> None:
        if not self.plan_id.strip():
            raise PrecisionGateError("plan_id is required")
        if self.preregistered_at.tzinfo is None or self.preregistered_at.utcoffset() is None:
            raise PrecisionGateError("preregistered_at must be timezone aware")
        if self.preregistered_at.date() >= self.first_eligible_session:
            raise PrecisionGateError("stopping plan must predate the first eligible session")
        if type(self.minimum_eligible_sessions) is not int or self.minimum_eligible_sessions < 60:
            raise PrecisionGateError("minimum eligible sessions must be at least the provisional 60-session floor")
        if type(self.maximum_eligible_sessions) is not int or self.maximum_eligible_sessions < self.minimum_eligible_sessions:
            raise PrecisionGateError("maximum sessions must be an integer at least the minimum")
        if not math.isfinite(self.target_ci_half_width) or self.target_ci_half_width <= 0:
            raise PrecisionGateError("target CI half-width must be finite and positive")
        if not math.isfinite(self.confidence_level) or not 0.5 < self.confidence_level < 1:
            raise PrecisionGateError("confidence level must be in (0.5, 1)")

    def digest(self) -> str:
        body = {"plan_id": self.plan_id,
                "preregistered_at": self.preregistered_at.astimezone(timezone.utc).isoformat(),
                "first_eligible_session": self.first_eligible_session.isoformat(),
                "minimum_eligible_sessions": self.minimum_eligible_sessions,
                "maximum_eligible_sessions": self.maximum_eligible_sessions,
                "target_ci_half_width": self.target_ci_half_width,
                "confidence_level": self.confidence_level}
        return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class SessionEligibility:
    session: date
    eligible: bool


@dataclass(frozen=True)
class PrecisionDecision:
    plan_hash: str
    eligible_sessions: int
    excluded_sessions: int
    ci_half_width: float
    decision: str
    promotion_allowed: bool
    reason: str


def decide_precision_stop(plan: PrecisionPlan, sessions: tuple[SessionEligibility, ...], *,
                          ci_lower: float, ci_upper: float,
                          interval_confidence_level: float) -> PrecisionDecision:
    """Return CONTINUE, INCONCLUSIVE_AT_MAX or REVIEW_ONLY; never self-promote."""
    if not math.isfinite(ci_lower) or not math.isfinite(ci_upper) or ci_lower > ci_upper:
        raise PrecisionGateError("confidence interval bounds must be finite and ordered")
    if interval_confidence_level != plan.confidence_level:
        raise PrecisionGateError("confidence interval level differs from preregistered plan")
    dates = tuple(row.session for row in sessions)
    if len(set(dates)) != len(dates) or tuple(sorted(dates)) != dates:
        raise PrecisionGateError("session observations must be unique and chronological")
    if any(day < plan.first_eligible_session for day in dates):
        raise PrecisionGateError("observations precede the preregistered cohort start")
    eligible = sum(row.eligible for row in sessions)
    excluded = len(sessions) - eligible
    if eligible > plan.maximum_eligible_sessions:
        raise PrecisionGateError("sample exceeded preregistered maximum; do not inspect extra observations")
    half_width = (ci_upper - ci_lower) / 2.0
    if eligible < plan.minimum_eligible_sessions:
        decision = "CONTINUE"
        reason = "provisional minimum eligible-session floor not reached"
    elif half_width > plan.target_ci_half_width and eligible < plan.maximum_eligible_sessions:
        decision = "CONTINUE"
        reason = "predeclared precision target not met; continue to the maximum sample"
    elif half_width > plan.target_ci_half_width:
        decision = "INCONCLUSIVE_AT_MAX"
        reason = "precision target not met at the preregistered maximum"
    else:
        decision = "REVIEW_ONLY"
        reason = "precision target reached; 60-session floor is not proof and independent promotion gates remain"
    return PrecisionDecision(plan.digest(), eligible, excluded, half_width, decision, False, reason)
