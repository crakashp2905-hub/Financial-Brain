from datetime import date, datetime, timedelta, timezone

import pytest

from financial_brain.forward_edge.risk_throttle import (
    RiskThrottleError,
    RiskThrottlePlan,
    RiskThrottleRegistry,
    calculate_risk_throttle,
)


def _plan(*, multipliers=None):
    return RiskThrottlePlan("risk-v1", datetime(2026, 11, 15, tzinfo=timezone.utc),
                            date(2026, 11, 16), 0.20,
                            multipliers or {"CALM": 0.8, "STRESS": 0.4})


def _decision(plan, *, regime="CALM", vol=0.10, observed=None):
    decision_at = datetime(2026, 11, 16, 4, tzinfo=timezone.utc)
    return calculate_risk_throttle(plan, session=date(2026, 11, 16), regime=regime,
                                   realized_volatility=vol,
                                   regime_observed_at=observed or decision_at - timedelta(minutes=1),
                                   decision_at=decision_at, regime_source_hash="snapshot-sha")


def test_frozen_regime_and_volatility_thresholds_produce_bounded_repeatable_size() -> None:
    plan = _plan()
    calm = _decision(plan)
    high_vol = _decision(plan, vol=0.40)
    assert calm.final_multiplier == 0.8
    assert high_vol.final_multiplier == 0.4
    assert high_vol.reason == "VOLATILITY_TARGET_REDUCTION"
    assert calm.plan_hash == high_vol.plan_hash == plan.digest()


def test_unknown_regime_and_future_input_fail_closed() -> None:
    plan = _plan()
    unknown = _decision(plan, regime="REGIME_UNKNOWN")
    assert unknown.final_multiplier == 0
    assert unknown.reason == "UNKNOWN_REGIME_NO_RISK"
    with pytest.raises(RiskThrottleError, match="future regime"):
        _decision(plan, observed=datetime(2026, 11, 16, 5, tzinfo=timezone.utc))


def test_threshold_plan_is_immutable_and_must_be_frozen_pre_cohort(tmp_path) -> None:
    registry = RiskThrottleRegistry(tmp_path / "throttle.sqlite3")
    plan = _plan()
    assert registry.freeze(plan)
    assert not registry.freeze(plan)
    with pytest.raises(RiskThrottleError, match="immutable"):
        registry.freeze(_plan(multipliers={"CALM": 0.5, "STRESS": 0.2}))
    with pytest.raises(RiskThrottleError, match="predate"):
        RiskThrottlePlan("late", datetime(2026, 11, 16, tzinfo=timezone.utc),
                         date(2026, 11, 16), 0.20, {"CALM": 1.0})
    registry.close()
