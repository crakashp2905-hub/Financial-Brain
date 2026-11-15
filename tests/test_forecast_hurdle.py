from datetime import date
from dataclasses import replace

import numpy as np
import pytest

from financial_brain.decisions.net_alpha import NetAlpha
from financial_brain.forward_edge.forecast_hurdle import FrozenForecastHurdle
from financial_brain.forward_edge.signal_allocator import SignalAllocatorError, allocate_signals


def _policy() -> FrozenForecastHurdle:
    return FrozenForecastHurdle(
        policy_id="momentum-hurdle-v1", min_net_alpha=0.004,
        training_start=date(2024, 1, 1), training_end=date(2025, 12, 31),
        validation_start=date(2026, 1, 1), training_data_sha256="a" * 64,
        selection_method="predeclared training-only net-alpha grid",
    )


def _allocate(policy: FrozenForecastHurdle, pin: str, *, as_of: date = date(2026, 1, 5)):
    forecast = NetAlpha(horizon=20, expected_return=0.05, benchmark=0.02,
                        fees=0.001, spread=0.001, impact=0.001, risk_penalty=0.0,
                        source="frozen-model", calibrated=True, calibration_id="cal-1")
    return allocate_signals(
        as_of=as_of, scheduled_rebalances=frozenset({as_of}), signals={"A": ("BUY", forecast)},
        covariance=np.eye(1), covariance_names=("A",), previous_weights={"CASH": 1.0},
        min_forecast_net_alpha=None, frozen_hurdle=policy, hurdle_policy_sha256=pin,
        max_name_weight=0.5, max_one_way_turnover=1.0, risk_aversion=0.0,
    )


def test_frozen_training_hurdle_is_applied_in_validation():
    policy = _policy()
    result = _allocate(policy, policy.sha256)
    assert result.counterfactuals[0].reason == "NET_ALPHA_ABOVE_HURDLE"
    assert dict(result.target.weights)["A"] > 0


def test_forward_drawdown_threshold_tweak_fails_the_existing_strategy_pin():
    pinned = _policy()
    changed = replace(pinned, min_net_alpha=0.0)
    with pytest.raises(SignalAllocatorError, match="frozen strategy pin"):
        _allocate(changed, pinned.sha256)


def test_training_hurdle_cannot_be_used_before_validation_or_with_mutable_threshold():
    policy = _policy()
    with pytest.raises(SignalAllocatorError, match="before validation"):
        _allocate(policy, policy.sha256, as_of=date(2025, 12, 31))
    with pytest.raises(SignalAllocatorError, match="no mutable threshold"):
        allocate_signals(
            as_of=date(2026, 1, 5), scheduled_rebalances=frozenset(), signals={},
            covariance=np.empty((0, 0)), covariance_names=(), previous_weights={"CASH": 1.0},
            min_forecast_net_alpha=0.0, frozen_hurdle=policy, hurdle_policy_sha256=policy.sha256,
        )


def test_hurdle_requires_ordered_training_validation_and_content_addressed_data():
    with pytest.raises(ValueError, match="strictly before"):
        FrozenForecastHurdle("bad", 0.01, date(2025, 1, 1), date(2026, 1, 1), date(2026, 1, 1),
                             "a" * 64, "training")
    with pytest.raises(ValueError, match="sha256"):
        FrozenForecastHurdle("bad", 0.01, date(2024, 1, 1), date(2025, 1, 1), date(2026, 1, 1),
                             "not-a-digest", "training")
