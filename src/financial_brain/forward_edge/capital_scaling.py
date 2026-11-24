"""Evidence-gated capital scaling review; a profitable week can never trigger scaling (FE-0141)."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime

from financial_brain.forward_edge.approvals import Approval, ApprovalError, validate_approval


class CapitalScalingError(PermissionError):
    """Capital scaling lacks required economic evidence or exact human authorization."""


@dataclass(frozen=True)
class ScaleReviewInput:
    strategy_id: str
    current_capital_inr: float
    proposed_capital_inr: float
    profitable_recent_week: bool
    independent_sessions: int
    minimum_independent_sessions: int
    net_alpha_lower_bound_bps: float
    supported_capacity_inr: float
    cost_schedule_sha256: str
    evidence_bundle_sha256: str
    independent_custody_verified: bool
    cost_calibration_verified: bool
    risk_limits_passed: bool

    def __post_init__(self) -> None:
        if not self.strategy_id.strip():
            raise CapitalScalingError("strategy ID is required")
        for value in (self.current_capital_inr, self.proposed_capital_inr,
                      self.supported_capacity_inr):
            if not math.isfinite(value) or value <= 0:
                raise CapitalScalingError("capital and capacity values must be finite and positive")
        if type(self.independent_sessions) is not int or self.independent_sessions < 0:
            raise CapitalScalingError("independent session count must be non-negative")
        if (type(self.minimum_independent_sessions) is not int
                or self.minimum_independent_sessions < 2):
            raise CapitalScalingError("minimum independent sessions must be at least two")
        if not math.isfinite(self.net_alpha_lower_bound_bps):
            raise CapitalScalingError("net-alpha lower bound must be finite")
        for name, digest in (("cost schedule", self.cost_schedule_sha256),
                             ("evidence bundle", self.evidence_bundle_sha256)):
            if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
                raise CapitalScalingError(f"{name} must carry lowercase SHA-256")


@dataclass(frozen=True)
class ScaleReview:
    strategy_id: str
    status: str
    reasons: tuple[str, ...]
    current_capital_inr: float
    proposed_capital_inr: float
    report_sha256: str
    approval_recorded: bool = False


def evaluate_scale_review(inputs: ScaleReviewInput) -> ScaleReview:
    """Require independent profitable net evidence, calibrated costs, capacity and risk gates."""
    reasons: set[str] = set()
    if inputs.proposed_capital_inr <= inputs.current_capital_inr:
        reasons.add("PROPOSED_CAPITAL_NOT_GREATER_THAN_CURRENT")
    if inputs.independent_sessions < inputs.minimum_independent_sessions:
        reasons.add("INSUFFICIENT_INDEPENDENT_SESSIONS")
    if inputs.net_alpha_lower_bound_bps <= 0:
        reasons.add("NET_ALPHA_LOWER_BOUND_NOT_POSITIVE")
    if inputs.proposed_capital_inr > inputs.supported_capacity_inr:
        reasons.add("PROPOSED_CAPITAL_EXCEEDS_SUPPORTED_CAPACITY")
    if not inputs.independent_custody_verified:
        reasons.add("INDEPENDENT_CUSTODY_NOT_VERIFIED")
    if not inputs.cost_calibration_verified:
        reasons.add("COST_CALIBRATION_NOT_VERIFIED")
    if not inputs.risk_limits_passed:
        reasons.add("RISK_LIMITS_NOT_PASSED")
    if not reasons:
        reasons.add("SEPARATE_HUMAN_CAPITAL_APPROVAL_REQUIRED")
    ordered = tuple(sorted(reasons))
    status = "REVIEW_ELIGIBLE" if ordered == ("SEPARATE_HUMAN_CAPITAL_APPROVAL_REQUIRED",) else "NO_SCALE"
    body = {"strategy_id": inputs.strategy_id, "status": status, "reasons": list(ordered),
            "current_capital_inr": inputs.current_capital_inr,
            "proposed_capital_inr": inputs.proposed_capital_inr,
            "profitable_recent_week": inputs.profitable_recent_week,
            "independent_sessions": inputs.independent_sessions,
            "minimum_independent_sessions": inputs.minimum_independent_sessions,
            "net_alpha_lower_bound_bps": inputs.net_alpha_lower_bound_bps,
            "supported_capacity_inr": inputs.supported_capacity_inr,
            "cost_schedule_sha256": inputs.cost_schedule_sha256,
            "evidence_bundle_sha256": inputs.evidence_bundle_sha256,
            "independent_custody_verified": inputs.independent_custody_verified,
            "cost_calibration_verified": inputs.cost_calibration_verified,
            "risk_limits_passed": inputs.risk_limits_passed,
            "approval_recorded": False}
    digest = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                       allow_nan=False).encode()).hexdigest()
    return ScaleReview(inputs.strategy_id, status, ordered, inputs.current_capital_inr,
                       inputs.proposed_capital_inr, digest)


def validate_scale_approval(review: ScaleReview, approval: Approval, *, now: datetime) -> ScaleReview:
    """Validate a separate exact-scope human CAPITAL approval; never changes active capital."""
    if review.status != "REVIEW_ELIGIBLE":
        raise CapitalScalingError("scale review is not eligible for human approval")
    try:
        validate_approval(approval, kind="CAPITAL", subject=review.strategy_id,
                          scope_sha256=review.report_sha256, now=now)
    except ApprovalError as exc:
        raise CapitalScalingError(f"capital approval rejected: {exc}") from exc
    return ScaleReview(review.strategy_id, "HUMAN_APPROVED_PENDING_SEPARATE_DEPLOYMENT",
                       review.reasons, review.current_capital_inr, review.proposed_capital_inr,
                       review.report_sha256, True)
