from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from financial_brain.forward_edge.uncertainty_sizing import (
    UncertaintySizingError,
    UncertaintySizingPolicy,
    UncertaintySizingRegistry,
    calibrate_uncertainty_policy,
    uncertainty_size_multiplier,
)


def _rows(count: int = 30) -> list[dict[str, object]]:
    return [
        {"split": "DEVELOPMENT", "exchange_date": date(2026, 10, 1).isoformat(),
         "forecast_net_alpha": 0.02, "uncertainty_standard_error": 0.01,
         "realized_net_alpha": 0.02 + 0.015,
         "decision_sha256": f"{index:064x}"}
        for index in range(1, count + 1)
    ]


def _policy() -> UncertaintySizingPolicy:
    return calibrate_uncertainty_policy(
        strategy_sha256="a" * 64, cohort_id="development-demo", observations=_rows(),
        validation_start=date(2026, 12, 1), frozen_at=datetime(2026, 11, 1, tzinfo=timezone.utc),
        calibration_protocol_sha256="b" * 64,
    )


def test_uncertainty_calibration_uses_development_data_and_never_deflates_error():
    policy = _policy()
    assert policy.standardized_error_multiplier == pytest.approx(1.5)
    assert policy.development_end < policy.validation_start
    assert policy.frozen_at_utc < "2026-12-01"


def test_uncertainty_calibration_rejects_validation_rows_and_duplicate_decisions():
    rows = _rows()
    rows[0] = {**rows[0], "split": "VALIDATION"}
    with pytest.raises(UncertaintySizingError, match="DEVELOPMENT"):
        calibrate_uncertainty_policy(
            strategy_sha256="a" * 64, cohort_id="cohort", observations=rows,
            validation_start=date(2026, 12, 1),
            frozen_at=datetime(2026, 11, 1, tzinfo=timezone.utc),
            calibration_protocol_sha256="b" * 64,
        )
    rows = _rows()
    rows[1] = {**rows[1], "decision_sha256": rows[0]["decision_sha256"]}
    with pytest.raises(UncertaintySizingError, match="unique SHA-256"):
        calibrate_uncertainty_policy(
            strategy_sha256="a" * 64, cohort_id="cohort", observations=rows,
            validation_start=date(2026, 12, 1),
            frozen_at=datetime(2026, 11, 1, tzinfo=timezone.utc),
            calibration_protocol_sha256="b" * 64,
        )


def test_sizing_multiplier_is_bounded_and_falls_as_uncertainty_rises():
    policy = _policy()
    low = uncertainty_size_multiplier(expected_net_alpha=0.02, uncertainty_standard_error=0.001,
                                      policy=policy)
    high = uncertainty_size_multiplier(expected_net_alpha=0.02, uncertainty_standard_error=0.01,
                                       policy=policy)
    assert 0 <= high < low <= 1
    assert uncertainty_size_multiplier(expected_net_alpha=0.02, uncertainty_standard_error=0,
                                        policy=policy) == 1
    assert uncertainty_size_multiplier(expected_net_alpha=0.001, uncertainty_standard_error=0.01,
                                        policy=policy) == 0


def test_frozen_uncertainty_policy_cannot_be_replaced(tmp_path):
    registry = UncertaintySizingRegistry(tmp_path / "uncertainty.jsonl")
    policy = _policy()
    assert registry.freeze(policy) == policy.sha256
    assert registry.resolve(policy.strategy_sha256, policy.cohort_id) == policy
    changed = UncertaintySizingPolicy(**{
        **policy.as_dict(), "standardized_error_multiplier": policy.standardized_error_multiplier + 0.1,
    })
    with pytest.raises(UncertaintySizingError, match="cannot be changed"):
        registry.freeze(changed)
