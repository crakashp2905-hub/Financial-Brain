import pytest

from financial_brain.learning.off_policy import (
    LoggedBanditDecision,
    OffPolicyError,
    evaluate_logged_policy,
)


def _balanced_logs() -> tuple[LoggedBanditDecision, ...]:
    rows = []
    for i in range(500):
        rows.append(LoggedBanditDecision(
            f"a-{i}", "A", 1.0 if i < 250 else 0.6,
            {"A": 1.0, "B": 0.0}, {"A": 0.5, "B": 0.5}, {"A": 0.8, "B": 0.2},
        ))
        rows.append(LoggedBanditDecision(
            f"b-{i}", "B", 0.2,
            {"A": 1.0, "B": 0.0}, {"A": 0.5, "B": 0.5}, {"A": 0.8, "B": 0.2},
        ))
    return tuple(rows)


def test_supported_estimators_match_exact_synthetic_target_value() -> None:
    result = evaluate_logged_policy(_balanced_logs(), variant_id="synthetic-unclipped-v1")
    assert result["status"] == "OPE_ESTIMATE"
    estimates = result["estimates"]
    assert estimates["direct_method"] == pytest.approx(0.8)
    assert estimates["ips"] == pytest.approx(0.8)
    assert estimates["self_normalized_ips"] == pytest.approx(0.8)
    assert estimates["doubly_robust"] == pytest.approx(0.8)
    assert result["effective_sample_size"] == pytest.approx(500)


def test_clipping_is_an_explicit_variant_and_reports_sensitivity_inputs() -> None:
    result = evaluate_logged_policy(
        _balanced_logs(), variant_id="synthetic-clip-1-v1", max_importance_weight=1.0,
    )
    assert result["variant_id"] == "synthetic-clip-1-v1"
    assert result["clipped_weight_count"] == 500
    assert result["max_raw_importance_weight"] == pytest.approx(2.0)
    assert result["max_used_importance_weight"] == pytest.approx(1.0)
    assert result["estimates"]["ips"] == pytest.approx(0.4)
    assert result["estimates"]["self_normalized_ips"] == pytest.approx(0.8)


def test_missing_propensity_and_unsupported_action_return_no_valid_ope() -> None:
    missing = LoggedBanditDecision("missing", "A", 1.0, {"A": 1.0}, None)
    result = evaluate_logged_policy((missing,), variant_id="missing-propensity-v1")
    assert result["status"] == "NO_VALID_OPE"
    assert result["reason"] == "BEHAVIOR_PROPENSITY_MISSING"

    unsupported = LoggedBanditDecision(
        "unsupported", "A", 1.0, {"A": 0.5, "B": 0.5}, {"A": 1.0, "B": 0.0},
    )
    result = evaluate_logged_policy((unsupported,), variant_id="unsupported-action-v1")
    assert result["status"] == "NO_VALID_OPE"
    assert result["reason"] == "TARGET_ACTION_OUTSIDE_BEHAVIOR_SUPPORT"
    assert result["estimates"] == {}


def test_low_effective_sample_size_refuses_estimates() -> None:
    result = evaluate_logged_policy(
        _balanced_logs(), variant_id="synthetic-ess-v1", minimum_effective_sample_size=501,
    )
    assert result["status"] == "NO_VALID_OPE"
    assert result["reason"] == "INSUFFICIENT_WEIGHTED_SUPPORT"
    assert result["estimates"] == {}


def test_invalid_probabilities_and_duplicate_ids_are_rejected() -> None:
    with pytest.raises(OffPolicyError, match="sum to one"):
        LoggedBanditDecision("bad", "A", 0.0, {"A": 0.7, "B": 0.2}, {"A": 0.5, "B": 0.5})
    row = LoggedBanditDecision("same", "A", 0.0, {"A": 1.0}, {"A": 1.0})
    with pytest.raises(OffPolicyError, match="unique"):
        evaluate_logged_policy((row, row), variant_id="duplicate-v1")
