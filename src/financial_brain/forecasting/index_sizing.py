"""Independent prospective-evidence gate before forecast confidence affects paper option size."""
from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from financial_brain.forward_edge.claim_tier import ClaimTier, ClaimTierError, TierEvidence, qualify_claim
from financial_brain.forward_edge.custody import (CustodyStatus, Receipt, TrustRoot,
                                                   classify_custody)

_SHA = re.compile(r"^[0-9a-f]{64}$")


class ForecastSizingError(ValueError):
    """Invalid prospective sample evidence or paper confidence-sizing input."""


@dataclass(frozen=True)
class ProspectiveSizingEvidence:
    strategy_id: str
    model_sha256: str
    sample_manifest_sha256: str
    sample_finalized_at: datetime
    option_decision_at: datetime
    prospective_sessions: int
    minimum_sessions: int
    predictive_skill_status: str
    calibration_error_upper_95: float
    maximum_calibration_error: float
    receipt: Receipt | None
    trust_roots: Mapping[tuple[str, str], TrustRoot]


@dataclass(frozen=True)
class SizingEligibility:
    eligible: bool
    status: str
    reasons: tuple[str, ...]
    strategy_id: str
    model_sha256: str
    sample_manifest_sha256: str
    custody_status: str


def evaluate_prospective_sizing_evidence(evidence: ProspectiveSizingEvidence) -> SizingEligibility:
    """Require an independently timestamped 60+ session prospective skill/calibration sample."""
    if not evidence.strategy_id.strip() or not _SHA.fullmatch(evidence.model_sha256):
        raise ForecastSizingError("strategy and model SHA-256 are required")
    if not _SHA.fullmatch(evidence.sample_manifest_sha256):
        raise ForecastSizingError("sample manifest SHA-256 is required")
    for stamp in (evidence.sample_finalized_at, evidence.option_decision_at):
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ForecastSizingError("sample and option decision timestamps must be timezone-aware")
    if type(evidence.prospective_sessions) is not int or evidence.prospective_sessions < 0:
        raise ForecastSizingError("prospective session count must be a nonnegative integer")
    if type(evidence.minimum_sessions) is not int or evidence.minimum_sessions < 60:
        raise ForecastSizingError("independent sizing requires a minimum of 60 sessions")
    if (not math.isfinite(evidence.calibration_error_upper_95)
            or not math.isfinite(evidence.maximum_calibration_error)
            or not 0 < evidence.maximum_calibration_error < 1):
        raise ForecastSizingError("finite calibration error bound and policy target are required")

    reasons = set()
    if evidence.sample_finalized_at >= evidence.option_decision_at:
        reasons.add("SAMPLE_NOT_FINALIZED_BEFORE_OPTION_DECISION")
    if evidence.prospective_sessions < evidence.minimum_sessions:
        reasons.add("INSUFFICIENT_INDEPENDENT_PROSPECTIVE_SESSIONS")
    if evidence.predictive_skill_status != "PREDICTIVE_SKILL_VERIFIED":
        reasons.add("PREDICTIVE_SKILL_GATE_NOT_PASSED")
    if evidence.calibration_error_upper_95 > evidence.maximum_calibration_error:
        reasons.add("CONFIDENCE_NOT_CALIBRATED_TO_POLICY")

    custody = classify_custody(
        manifest_sha256=evidence.sample_manifest_sha256,
        decision_at_utc=evidence.sample_finalized_at,
        entry_earliest_at_utc=evidence.option_decision_at,
        receipt=evidence.receipt, trust_roots=evidence.trust_roots)
    try:
        qualify_claim(ClaimTier.INDEPENDENT_FORWARD, TierEvidence(
            independent_receipts_verified=custody is CustodyStatus.VERIFIED,
            eligible_independent_sessions=evidence.prospective_sessions))
    except ClaimTierError:
        reasons.add("INDEPENDENT_FORWARD_EVIDENCE_NOT_QUALIFIED")
    if custody is not CustodyStatus.VERIFIED:
        reasons.add("INDEPENDENT_CUSTODY_NOT_VERIFIED")
    ordered = tuple(sorted(reasons))
    return SizingEligibility(not ordered, "ELIGIBLE" if not ordered else "CONFIDENCE_SIZING_DISABLED",
                             ordered, evidence.strategy_id, evidence.model_sha256,
                             evidence.sample_manifest_sha256, custody.value)


def confidence_adjusted_paper_lots(*, base_risk_capped_lots: int, calibrated_confidence: float,
                                   eligibility: SizingEligibility) -> dict[str, int | str | bool]:
    """Scale a precomputed risk cap down by calibrated confidence, fail-closed to zero lots."""
    if type(base_risk_capped_lots) is not int or base_risk_capped_lots < 0:
        raise ForecastSizingError("base risk-capped lots must be a nonnegative integer")
    if not math.isfinite(calibrated_confidence) or not 0 <= calibrated_confidence <= 1:
        raise ForecastSizingError("calibrated confidence must be in [0, 1]")
    if not eligibility.eligible:
        return {"status": "NO_TRADE", "lots": 0,
                "reason": "CONFIDENCE_SIZING_DISABLED", "evidence_sha256": eligibility.sample_manifest_sha256}
    lots = math.floor(base_risk_capped_lots * calibrated_confidence)
    return {"status": "PAPER_SIZE" if lots else "NO_TRADE", "lots": lots,
            "reason": "CALIBRATED_CONFIDENCE_SCALED_RISK_CAP" if lots else "ROUNDED_BELOW_ONE_LOT",
            "evidence_sha256": eligibility.sample_manifest_sha256}
