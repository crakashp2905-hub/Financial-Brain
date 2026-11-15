from datetime import datetime, timezone

import pytest

from financial_brain.forward_edge.contribution_registry import (
    ContributionRegistry,
    ContributionRegistryError,
)
from financial_brain.forward_edge.prospective_cohort import freeze_champion
from financial_brain.forward_edge.prospective_registry import ProspectiveCohortRegistry


def _registry(tmp_path):
    plan = freeze_champion(
        development_returns={"alpha": [0.01, 0.02]},
        development_sessions=["2026-01-05", "2026-01-06"],
        confirmation_sessions=["2026-01-07", "2026-01-08"],
        frozen_at=datetime(2026, 1, 6, 15, tzinfo=timezone.utc),
        first_confirmation_observation_at=datetime(2026, 1, 7, 3, tzinfo=timezone.utc),
    )
    cohorts = ProspectiveCohortRegistry(tmp_path / "cohorts.jsonl")
    cohorts.register("cohort-a", plan)
    registry = ContributionRegistry(tmp_path / "contributions.jsonl", cohorts)
    returns = {"2026-01-07": 0.01, "2026-01-08": -0.005}
    contributions = [
        {"contribution_id": "one", "session": "2026-01-07",
         "instrument": "ABC", "net_return_contribution": 0.01},
        {"contribution_id": "two", "session": "2026-01-08",
         "instrument": "ABC", "net_return_contribution": -0.005},
    ]
    args = dict(cohort_id="cohort-a", report_id="report-a",
                daily_net_returns=returns, contributions=contributions,
                source_sha256={"daily_returns": "a" * 64, "contributions": "b" * 64})
    return registry, cohorts, args


def test_cohort_bound_contribution_report_recovers_and_is_idempotent(tmp_path):
    registry, cohorts, args = _registry(tmp_path)
    report = registry.register(**args)
    seq = registry.journal.seq
    assert registry.register(**args) == report
    assert registry.journal.seq == seq
    recovered = ContributionRegistry(tmp_path / "contributions.jsonl", cohorts)
    assert recovered.resolve(cohort_id="cohort-a", report_id="report-a") == report


def test_registered_contribution_report_cannot_be_replaced(tmp_path):
    registry, _, args = _registry(tmp_path)
    registry.register(**args)
    changed = {**args, "daily_net_returns": {"2026-01-07": 0.02, "2026-01-08": -0.005},
               "contributions": [
                   {"contribution_id": "one", "session": "2026-01-07",
                    "instrument": "ABC", "net_return_contribution": 0.02},
                   args["contributions"][1],
               ]}
    with pytest.raises(ContributionRegistryError, match="immutable"):
        registry.register(**changed)


def test_missing_source_digest_and_unregistered_report_fail_closed(tmp_path):
    registry, _, args = _registry(tmp_path)
    with pytest.raises(ContributionRegistryError, match="require source digests"):
        registry.register(**{**args, "source_sha256": {"daily_returns": "a" * 64}})
    with pytest.raises(ContributionRegistryError, match="no registered"):
        registry.resolve(cohort_id="cohort-a", report_id="missing")
