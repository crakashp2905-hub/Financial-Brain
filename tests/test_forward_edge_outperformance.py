import pytest

from financial_brain.forward_edge.outperformance import (
    AlphaInterval,
    OutperformanceError,
    assess_dual_benchmark_outperformance,
)


def _interval(mean, low, high):
    return AlphaInterval(mean, low, high, 0.95, "STATIONARY_BLOCK_CI")


def test_positive_mean_is_insufficient_if_either_lower_bound_includes_zero() -> None:
    report = assess_dual_benchmark_outperformance(
        cash=_interval(0.02, 0.001, 0.04),
        risk_matched_benchmark=_interval(0.01, -0.002, 0.03),
        eligible_sessions=80, independent_receipts_verified=True,
        evidence_tier="INDEPENDENT_FORWARD")
    assert report.statistical_status == "INSUFFICIENT_LCB_INTERVAL_INCLUDES_ZERO_OR_NEGATIVE"
    assert not report.promotion_allowed


def test_dual_positive_lcb_is_only_a_statistical_signal_not_promotion() -> None:
    report = assess_dual_benchmark_outperformance(
        cash=_interval(0.03, 0.01, 0.05),
        risk_matched_benchmark=_interval(0.02, 0.005, 0.04),
        eligible_sessions=100, independent_receipts_verified=True,
        evidence_tier="INDEPENDENT_FORWARD")
    assert report.statistical_status == "POSITIVE_LCB_AGAINST_BOTH_STATISTICAL_SIGNAL_ONLY"
    assert not report.promotion_allowed


def test_sample_and_receipt_gates_and_interval_consistency_are_enforced() -> None:
    args = dict(cash=_interval(0.03, 0.01, 0.05),
                risk_matched_benchmark=_interval(0.02, 0.005, 0.04),
                independent_receipts_verified=False, evidence_tier="LOCAL_PAPER")
    assert assess_dual_benchmark_outperformance(**args, eligible_sessions=59).statistical_status == "INSUFFICIENT_ELIGIBLE_SESSIONS"
    assert assess_dual_benchmark_outperformance(**args, eligible_sessions=60).statistical_status == "INDEPENDENT_FORWARD_ATTESTATION_MISSING"
    with pytest.raises(OutperformanceError, match="same confidence"):
        assess_dual_benchmark_outperformance(
            cash=_interval(0.03, 0.01, 0.05),
            risk_matched_benchmark=AlphaInterval(0.02, 0.005, 0.04, 0.90, "OTHER"),
            eligible_sessions=60, independent_receipts_verified=False,
            evidence_tier="LOCAL_PAPER")
