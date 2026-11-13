from datetime import date

import numpy as np
import pytest

from financial_brain.decisions.net_alpha import NetAlpha
from financial_brain.forward_edge.signal_allocator import SignalAllocatorError, allocate_signals
from financial_brain.forward_edge.uncertainty_sizing import UncertaintySizingPolicy


DAY = date(2027, 1, 4)


def _alpha(net: float, horizon: int = 20) -> NetAlpha:
    return NetAlpha(horizon=horizon, expected_return=0.03 + net, benchmark=0.03,
                    fees=0.001, spread=0.001, impact=0.001, risk_penalty=0.0,
                    source="frozen-research-forecast")


def _allocate(signals, *, previous=None, **over):
    args = dict(as_of=DAY, scheduled_rebalances=frozenset({DAY}), signals=signals,
                covariance=np.eye(len(signals)) * 0.02, covariance_names=tuple(sorted(signals)),
                previous_weights=previous or {"CASH": 1.0}, min_forecast_net_alpha=0.002,
                max_name_weight=0.5, max_one_way_turnover=1.0, risk_aversion=0.0,
                turnover_cost_per_unit=0.0)
    args.update(over)
    return allocate_signals(**args)


def test_signal_to_target_uses_one_costed_alpha_allocator_and_explicit_cash():
    result = _allocate({"A": ("BUY", _alpha(0.02)), "B": ("BUY", _alpha(0.001))})
    weights = dict(result.target.weights)
    dispositions = {name: reason for name, reason, _ in result.dispositions}
    assert weights["A"] > 0
    assert weights["B"] == 0
    assert dispositions == {"A": "NET_ALPHA_ABOVE_HURDLE", "B": "NO_TRADE_NET_ALPHA_HURDLE"}
    assert weights["CASH"] >= 0
    assert result.research_only is True


def test_sell_signal_reduces_existing_position_and_no_signal_is_not_a_new_entry():
    result = _allocate({"A": ("SELL", _alpha(0.02)), "B": ("NO_SIGNAL", _alpha(0.02))},
                       previous={"A": 0.2, "B": 0.0, "CASH": 0.8})
    weights = dict(result.target.weights)
    assert weights["A"] < 0.2
    assert weights["B"] == 0


def test_holds_must_be_observed_and_forecast_horizons_must_match():
    with pytest.raises(SignalAllocatorError, match="held instrument"):
        _allocate({"B": ("BUY", _alpha(0.02))}, previous={"A": 0.1, "CASH": 0.9})
    with pytest.raises(SignalAllocatorError, match="same holding horizon"):
        _allocate({"A": ("BUY", _alpha(0.02, 5)), "B": ("BUY", _alpha(0.03, 20))})


def test_hurdle_and_covariance_universe_must_be_valid():
    with pytest.raises(SignalAllocatorError, match="hurdle"):
        _allocate({"A": ("BUY", _alpha(0.02))}, min_forecast_net_alpha=-0.1)
    with pytest.raises(SignalAllocatorError, match="covariance universe"):
        _allocate({"A": ("BUY", _alpha(0.02)), "B": ("BUY", _alpha(0.03))},
                  covariance_names=("A",))


def test_signal_allocator_forwards_position_and_sector_limits():
    result = _allocate(
        {name: ("BUY", _alpha(0.02 - i * 0.001)) for i, name in enumerate(("A", "B", "C"))},
        max_positions=1,
        sectors={"A": "technology", "B": "technology", "C": "consumer"},
        max_sector_weight=0.25,
    )
    weights = dict(result.target.weights)
    assert sum(weight > 1e-12 for name, weight in weights.items() if name != "CASH") <= 1
    assert weights["A"] + weights["B"] <= 0.25 + 1e-8
    assert weights["CASH"] >= 0.75 - 1e-8


def test_frozen_uncertainty_haircut_reduces_costed_signal_weight():
    policy = UncertaintySizingPolicy(
        strategy_sha256="a" * 64, cohort_id="demo", standardized_error_multiplier=1.0,
        confidence_z=1.0, development_data_sha256="b" * 64,
        calibration_protocol_sha256="c" * 64, development_start="2026-10-01",
        development_end="2026-11-01", validation_start="2026-12-01", observation_count=30,
        frozen_at_utc="2026-11-15T00:00:00+00:00",
    )
    signals = {"A": ("BUY", _alpha(0.02))}
    plain = _allocate(signals, risk_aversion=10.0)
    adjusted = _allocate(
        signals, risk_aversion=10.0, uncertainty_policy=policy,
        uncertainty_standard_errors={"A": 0.005},
    )
    assert dict(adjusted.target.weights)["A"] < dict(plain.target.weights)["A"]
    assert dict(adjusted.target.weights)["CASH"] > dict(plain.target.weights)["CASH"]
    assert adjusted.uncertainty_multipliers[0][1] == pytest.approx(1.0 - 0.005 / 0.017)


def test_uncertainty_input_without_frozen_policy_fails_closed():
    with pytest.raises(SignalAllocatorError, match="must be supplied together"):
        _allocate({"A": ("BUY", _alpha(0.02))}, uncertainty_standard_errors={"A": 0.001})
