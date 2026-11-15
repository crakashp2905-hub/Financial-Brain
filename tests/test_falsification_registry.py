from datetime import datetime, timezone

import pytest

from financial_brain.forward_edge.evidence_stats import FALSIFICATION_CHECKS
from financial_brain.forward_edge.falsification_registry import (
    FalsificationRegistry,
    FalsificationRegistryError,
)
from financial_brain.forward_edge.prospective_cohort import freeze_champion
from financial_brain.forward_edge.prospective_registry import ProspectiveCohortRegistry


def _cohort_registry(path):
    cohort = freeze_champion(
        development_returns={"candidate": [0.01, 0.02]},
        development_sessions=["2025-12-30", "2025-12-31"],
        confirmation_sessions=["2026-01-02", "2026-01-03"],
        frozen_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        first_confirmation_observation_at=datetime(2026, 1, 2, 3, tzinfo=timezone.utc),
    )
    registry = ProspectiveCohortRegistry(path)
    registry.register("falsification-cohort", cohort)
    return registry


def test_complete_report_is_durably_bound_to_the_frozen_cohort_and_recovered(tmp_path):
    cohort_registry = _cohort_registry(tmp_path / "cohort.jsonl")
    path = tmp_path / "falsification.jsonl"
    registry = FalsificationRegistry(path, cohort_registry)
    registered = registry.register(
        trial_id="trial-1", cohort_id="falsification-cohort",
        results={check: True for check in FALSIFICATION_CHECKS},
        artifact_sha256={check: "a" * 64 for check in FALSIFICATION_CHECKS},
    )
    assert registered["status"] == "NO_FALSIFICATION_FOUND"
    recovered = FalsificationRegistry(path, cohort_registry)
    assert recovered.resolve(trial_id="trial-1", cohort_id="falsification-cohort") == registered


def test_incomplete_or_failed_checks_are_not_registered_as_clean(tmp_path):
    cohort_registry = _cohort_registry(tmp_path / "cohort.jsonl")
    registry = FalsificationRegistry(tmp_path / "falsification.jsonl", cohort_registry)
    incomplete = {check: True for check in FALSIFICATION_CHECKS}
    incomplete["factor_overlap"] = None
    incomplete_report = registry.register(
        trial_id="incomplete", cohort_id="falsification-cohort", results=incomplete,
        artifact_sha256={check: "a" * 64 for check in FALSIFICATION_CHECKS if incomplete[check] is not None},
    )
    failed = {check: True for check in FALSIFICATION_CHECKS}
    failed["stale_quote"] = False
    failed_report = registry.register(
        trial_id="failed", cohort_id="falsification-cohort", results=failed,
        artifact_sha256={check: "b" * 64 for check in FALSIFICATION_CHECKS},
    )
    assert incomplete_report["status"] == "INSUFFICIENT_EVIDENCE"
    assert failed_report["status"] == "FAIL"


def test_registered_result_requires_an_artifact_digest_and_cannot_be_replaced(tmp_path):
    cohort_registry = _cohort_registry(tmp_path / "cohort.jsonl")
    registry = FalsificationRegistry(tmp_path / "falsification.jsonl", cohort_registry)
    results = {check: True for check in FALSIFICATION_CHECKS}
    digests = {check: "a" * 64 for check in FALSIFICATION_CHECKS}
    with pytest.raises(FalsificationRegistryError, match="requires a source artifact digest"):
        registry.register(trial_id="trial-1", cohort_id="falsification-cohort",
                          results=results, artifact_sha256={})
    registry.register(trial_id="trial-1", cohort_id="falsification-cohort",
                      results=results, artifact_sha256=digests)
    changed = dict(digests)
    changed["lookahead"] = "b" * 64
    with pytest.raises(FalsificationRegistryError, match="immutable"):
        registry.register(trial_id="trial-1", cohort_id="falsification-cohort",
                          results=results, artifact_sha256=changed)
