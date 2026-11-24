from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.forward_edge.approvals import Approval
from financial_brain.forward_edge.capital_scaling import (
    CapitalScalingError,
    ScaleReviewInput,
    evaluate_scale_review,
    validate_scale_approval,
)


NOW = datetime(2026, 11, 16, 4, tzinfo=timezone.utc)


def _inputs(**overrides):
    values = {"strategy_id": "swing-v2", "current_capital_inr": 10_000,
              "proposed_capital_inr": 15_000, "profitable_recent_week": False,
              "independent_sessions": 80, "minimum_independent_sessions": 60,
              "net_alpha_lower_bound_bps": 5, "supported_capacity_inr": 20_000,
              "cost_schedule_sha256": "a" * 64, "evidence_bundle_sha256": "b" * 64,
              "independent_custody_verified": True, "cost_calibration_verified": True,
              "risk_limits_passed": True}
    values.update(overrides)
    return ScaleReviewInput(**values)


def test_profitable_week_is_not_enough_and_full_evidence_still_needs_human_approval():
    short = evaluate_scale_review(_inputs(profitable_recent_week=True,
                                          independent_sessions=5))
    assert short.status == "NO_SCALE"
    assert "INSUFFICIENT_INDEPENDENT_SESSIONS" in short.reasons
    review = evaluate_scale_review(_inputs(profitable_recent_week=True))
    assert review.status == "REVIEW_ELIGIBLE"
    assert not review.approval_recorded
    approval = Approval("CAPITAL", "swing-v2", review.report_sha256, "Owner", "human",
                        NOW - timedelta(minutes=1), NOW + timedelta(hours=1))
    authorized = validate_scale_approval(review, approval, now=NOW)
    assert authorized.approval_recorded
    assert authorized.status == "HUMAN_APPROVED_PENDING_SEPARATE_DEPLOYMENT"
    assert authorized.current_capital_inr == review.current_capital_inr


def test_scale_rejects_negative_net_alpha_unsupported_capacity_and_bad_approval():
    review = evaluate_scale_review(_inputs(net_alpha_lower_bound_bps=-1,
                                           supported_capacity_inr=12_000))
    assert review.status == "NO_SCALE"
    assert "NET_ALPHA_LOWER_BOUND_NOT_POSITIVE" in review.reasons
    assert "PROPOSED_CAPITAL_EXCEEDS_SUPPORTED_CAPACITY" in review.reasons
    otherwise = evaluate_scale_review(_inputs())
    bad = Approval("CAPITAL", "swing-v2", "c" * 64, "Owner", "human",
                   NOW - timedelta(minutes=1), NOW + timedelta(hours=1))
    with pytest.raises(CapitalScalingError, match="approval rejected"):
        validate_scale_approval(otherwise, bad, now=NOW)


def test_scale_review_requires_custody_cost_and_risk_evidence():
    review = evaluate_scale_review(_inputs(independent_custody_verified=False,
                                           cost_calibration_verified=False,
                                           risk_limits_passed=False))
    assert review.status == "NO_SCALE"
    assert {"INDEPENDENT_CUSTODY_NOT_VERIFIED", "COST_CALIBRATION_NOT_VERIFIED",
            "RISK_LIMITS_NOT_PASSED"} <= set(review.reasons)
