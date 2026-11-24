from datetime import datetime, timezone

import pytest

from financial_brain.learning.world_model import (
    WorldModelError,
    WorldModelRolloutEvidence,
    distributional_calibration_gate,
)


def test_rollout_manifest_is_hash_pinned_world_model_only_and_never_promotion_eligible():
    record = WorldModelRolloutEvidence(
        "fixture-1", "a" * 64, "b" * 64, datetime(2026, 11, 24, tzinfo=timezone.utc),
        3, 100, (0.1, 0.2, 0.3),
    )
    manifest = record.manifest()
    assert manifest["evidence_status"] == "WORLD_MODEL_ONLY"
    assert manifest["promotion_allowed"] is False
    assert len(manifest["manifest_sha256"]) == 64


def test_rollout_cannot_be_relabelled_as_observed_or_promotion_eligible():
    with pytest.raises(WorldModelError, match="permanently non-promotion"):
        WorldModelRolloutEvidence(
            "fixture-1", "a" * 64, "b" * 64, datetime(2026, 11, 24, tzinfo=timezone.utc),
            1, 1, (0.1,), evidence_status="OBSERVED", promotion_allowed=True,
        )


def test_calibration_gate_retires_an_overconfident_forecast_independent_of_returns():
    report = distributional_calibration_gate(
        nominal_coverage={0.9: 0.9, 0.5: 0.5},
        observed_coverage={0.9: 0.62, 0.5: 0.45},
        pit_uniformity_pvalue=0.2,
        observations=500,
    )
    assert report.status == "RETIRE"
    assert "COVERAGE_ERROR_EXCEEDS_LIMIT" in report.reasons
    assert report.promotion_allowed is False


def test_calibration_gate_withholds_decision_on_small_sample_and_pit_failure():
    report = distributional_calibration_gate(
        nominal_coverage={0.9: 0.9}, observed_coverage={0.9: 0.88},
        pit_uniformity_pvalue=0.001, observations=30,
    )
    assert report.status == "RETIRE"
    assert report.reasons == ("INSUFFICIENT_CALIBRATION_SAMPLE", "PIT_UNIFORMITY_REJECTED")


def test_calibration_gate_only_grants_research_eligibility_after_all_checks():
    report = distributional_calibration_gate(
        nominal_coverage={0.9: 0.9, 0.5: 0.5},
        observed_coverage={0.9: 0.88, 0.5: 0.52},
        pit_uniformity_pvalue=0.2,
        observations=500,
    )
    assert report.status == "RESEARCH_ELIGIBLE"
    assert not report.reasons
    assert report.promotion_allowed is False


def test_calibration_gate_validates_inputs():
    with pytest.raises(WorldModelError, match="aligned"):
        distributional_calibration_gate(
            nominal_coverage={0.9: 0.9}, observed_coverage={},
            pit_uniformity_pvalue=0.5, observations=200,
        )
