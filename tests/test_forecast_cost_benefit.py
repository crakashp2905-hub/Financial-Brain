from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from financial_brain.forward_edge.forecast_cost_benefit import (
    CostBenefitError,
    ForecastBenefitPolicy,
    ForecastBenefitPolicyRegistry,
    assess_cost_to_benefit,
    calibrate_forecast_benefit_policy,
)


def _rows(count=30):
    return [{"split": "DEVELOPMENT", "exchange_date": "2026-10-01",
             "forecast_benefit": 0.02, "realized_excess": 0.01,
             "decision_sha256": f"{i:064x}", "forecast_model_sha256": "c" * 64}
            for i in range(1, count + 1)]


def _policy():
    return calibrate_forecast_benefit_policy(
        strategy_sha256="a" * 64, cohort_id="cohort", forecast_model_sha256="c" * 64,
        observations=_rows(), validation_start=date(2026, 12, 1),
        frozen_at=datetime(2026, 11, 1, tzinfo=timezone.utc),
        calibration_protocol_sha256="b" * 64,
    )


def test_benefit_calibration_uses_development_rows_and_cannot_inflate_forecast():
    policy = _policy()
    assert policy.conservative_benefit_scale == pytest.approx(0.5)
    rows = _rows()
    rows[0] = {**rows[0], "split": "VALIDATION"}
    with pytest.raises(CostBenefitError, match="DEVELOPMENT"):
        calibrate_forecast_benefit_policy(
            strategy_sha256="a" * 64, cohort_id="cohort", forecast_model_sha256="c" * 64,
            observations=rows, validation_start=date(2026, 12, 1),
            frozen_at=datetime(2026, 11, 1, tzinfo=timezone.utc),
            calibration_protocol_sha256="b" * 64,
        )


def test_cost_to_calibrated_benefit_ratio_and_zero_placeholder_fail_closed():
    policy = _policy()
    result = assess_cost_to_benefit(
        forecast_benefit=0.02, fees=0.001, spread=0.001, impact=0.001,
        forecast_model_sha256="c" * 64, session_date=date(2026, 12, 1),
        policy=policy, strategy_sha256="a" * 64, cohort_id="cohort",
    )
    assert result["approved"] is True
    assert result["ratio"] == pytest.approx(0.3)
    zero = assess_cost_to_benefit(
        forecast_benefit=0, fees=0.001, spread=0.001, impact=0.001,
        forecast_model_sha256="c" * 64, session_date=date(2026, 12, 1),
        policy=policy, strategy_sha256="a" * 64, cohort_id="cohort",
    )
    assert zero["approved"] is False
    assert zero["ratio"] is None
    assert zero["reason"] == "COST_EXCEEDS_CALIBRATED_BENEFIT"


def test_no_policy_wrong_model_and_prevalidation_session_abstain():
    args = dict(forecast_benefit=0.05, fees=0.001, spread=0.001, impact=0.001,
                forecast_model_sha256="c" * 64, session_date=date(2026, 12, 1),
                strategy_sha256="a" * 64, cohort_id="cohort")
    no_policy = assess_cost_to_benefit(**args, policy=None)
    assert no_policy["approved"] is False
    assert no_policy["reason"] == "FORECAST_BENEFIT_UNCALIBRATED"
    mismatch = assess_cost_to_benefit(**{**args, "forecast_model_sha256": "d" * 64}, policy=_policy())
    assert mismatch["approved"] is False
    early = assess_cost_to_benefit(**{**args, "session_date": date(2026, 11, 30)}, policy=_policy())
    assert early["approved"] is False


def test_frozen_benefit_policy_registry_is_immutable(tmp_path):
    registry = ForecastBenefitPolicyRegistry(tmp_path / "benefit-policy.jsonl")
    policy = _policy()
    assert registry.freeze(policy) == policy.sha256
    assert registry.resolve(policy.strategy_sha256, policy.cohort_id) == policy
    changed = ForecastBenefitPolicy(**{
        **policy.__dict__, "max_cost_to_benefit_ratio": 0.8,
    })
    with pytest.raises(CostBenefitError, match="cannot be replaced"):
        registry.freeze(changed)
