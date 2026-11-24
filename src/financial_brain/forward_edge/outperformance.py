"""Dual-benchmark lower-confidence-bound report for strategy net alpha (FE-0098)."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass


class OutperformanceError(ValueError):
    """Invalid confidence bounds or evidence lineage."""


@dataclass(frozen=True)
class AlphaInterval:
    mean_alpha: float
    lower_bound: float
    upper_bound: float
    confidence_level: float
    method: str

    def __post_init__(self) -> None:
        if not all(math.isfinite(value) for value in (self.mean_alpha, self.lower_bound,
                                                       self.upper_bound, self.confidence_level)):
            raise OutperformanceError("alpha interval values must be finite")
        if self.lower_bound > self.mean_alpha or self.mean_alpha > self.upper_bound:
            raise OutperformanceError("alpha mean must lie within the interval")
        if not 0.5 < self.confidence_level < 1 or not self.method.strip():
            raise OutperformanceError("confidence level and interval method are required")


@dataclass(frozen=True)
class OutperformanceAssessment:
    cash: AlphaInterval
    risk_matched_benchmark: AlphaInterval
    eligible_sessions: int
    independent_receipts_verified: bool
    evidence_tier: str
    statistical_status: str
    promotion_allowed: bool
    report_hash: str


def assess_dual_benchmark_outperformance(*, cash: AlphaInterval,
                                         risk_matched_benchmark: AlphaInterval,
                                         eligible_sessions: int,
                                         independent_receipts_verified: bool,
                                         evidence_tier: str) -> OutperformanceAssessment:
    """Report evidence strength; positive intervals never directly authorize promotion."""
    if type(eligible_sessions) is not int or eligible_sessions < 0:
        raise OutperformanceError("eligible_sessions must be a non-negative integer")
    allowed_tiers = {"HISTORICAL", "SHADOW", "LOCAL_PAPER", "INDEPENDENT_FORWARD", "DEPLOYMENT_VALIDATED"}
    if evidence_tier not in allowed_tiers:
        raise OutperformanceError("unknown evidence tier")
    if independent_receipts_verified and evidence_tier not in {"INDEPENDENT_FORWARD", "DEPLOYMENT_VALIDATED"}:
        raise OutperformanceError("independent receipts conflict with declared evidence tier")
    if cash.confidence_level != risk_matched_benchmark.confidence_level:
        raise OutperformanceError("both benchmark intervals must use the same confidence level")

    if eligible_sessions < 60:
        status = "INSUFFICIENT_ELIGIBLE_SESSIONS"
    elif not independent_receipts_verified:
        status = "INDEPENDENT_FORWARD_ATTESTATION_MISSING"
    elif cash.lower_bound <= 0 or risk_matched_benchmark.lower_bound <= 0:
        status = "INSUFFICIENT_LCB_INTERVAL_INCLUDES_ZERO_OR_NEGATIVE"
    else:
        status = "POSITIVE_LCB_AGAINST_BOTH_STATISTICAL_SIGNAL_ONLY"
    fields = {"cash": cash.__dict__, "risk_matched_benchmark": risk_matched_benchmark.__dict__,
              "eligible_sessions": eligible_sessions,
              "independent_receipts_verified": independent_receipts_verified,
              "evidence_tier": evidence_tier, "statistical_status": status,
              "promotion_allowed": False}
    digest = hashlib.sha256(json.dumps(fields, sort_keys=True, separators=(",", ":"),
                                      allow_nan=False).encode()).hexdigest()
    return OutperformanceAssessment(cash, risk_matched_benchmark, eligible_sessions,
                                    independent_receipts_verified, evidence_tier, status, False, digest)
