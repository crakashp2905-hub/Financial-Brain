from datetime import datetime, timezone

import pytest

from financial_brain.execution.oms.quality import (
    calibrate_slippage,
    register_calibrated_slippage,
    slippage_bucket,
)
from financial_brain.forward_edge.rehearsal import ExecutionModelRegistry, RehearsalError


def _observation(**overrides):
    row = {
        "spread_bp": 8.0,
        "adv_inr": 2_000_000_000.0,
        "time_minute": 610,
        "participation": 0.03,
        "volatility_pct": 1.5,
        "slippage_bp": 2.0,
        "fidelity": "OBSERVED_BROKER",
        "observed_at": datetime(2026, 11, 12, 5, 0, tzinfo=timezone.utc).isoformat(),
        "source_sha256": "a" * 64,
    }
    return {**row, **overrides}


def test_slippage_buckets_cover_each_requested_condition_and_keep_missing_explicit():
    labels = slippage_bucket(spread_bp=8, adv_inr=2e9, time_minute=610,
                             participation=0.03, volatility_pct=1.5)
    assert labels == ("5_15bp", "large", "midday_1000_1430", "1_5pct", "1_2pct")
    assert slippage_bucket(spread_bp=None, adv_inr=None, time_minute=None,
                           participation=None, volatility_pct=None) == (
        "unknown", "unknown", "unknown", "unknown", "unknown")


def test_calibration_uses_only_provenance_bearing_observed_broker_fills():
    observed = [_observation(slippage_bp=float(i)) for i in range(20)]
    simulated = _observation(fidelity="SIMULATED_QUOTE", source_sha256="b" * 64)
    missing_source = _observation(source_sha256=None)
    out = calibrate_slippage(observed + [simulated, missing_source])
    bucket = next(iter(out["buckets"].values()))
    assert bucket["status"] == "CALIBRATED"
    assert bucket["observed_broker_fills"] == 20
    assert bucket["excluded_unverified_rows"] == 2
    assert bucket["recommended_slippage_bp"] == pytest.approx(15.0)


def test_simulated_or_under_sampled_buckets_are_uncalibrated_and_emit_no_parameter():
    simulated_only = _observation(fidelity="SIMULATED_BAR")
    out = calibrate_slippage([simulated_only], min_observations=1)
    bucket = next(iter(out["buckets"].values()))
    assert bucket["status"] == "UNCALIBRATED"
    assert bucket["recommended_slippage_bp"] is None
    with pytest.raises(ValueError, match="min_observations"):
        calibrate_slippage([], min_observations=0)


def test_only_complete_observed_calibration_can_register_a_new_immutable_model():
    registry = ExecutionModelRegistry()
    uncalibrated = calibrate_slippage([_observation(fidelity="SIMULATED_QUOTE")], min_observations=1)
    with pytest.raises(ValueError, match="uncalibrated"):
        register_calibrated_slippage(uncalibrated, registry=registry, version="simulated")

    observed = calibrate_slippage([_observation(slippage_bp=float(i)) for i in range(20)])
    digest = register_calibrated_slippage(observed, registry=registry, version="observed-v1")
    assert len(digest) == 64
    assert registry.get("observed-v1")["source"] == "OBSERVED_BROKER_CALIBRATION"
    with pytest.raises(RehearsalError, match="version exists"):
        register_calibrated_slippage(observed, registry=registry, version="observed-v1")
