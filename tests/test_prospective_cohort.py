from datetime import datetime, timezone
from dataclasses import replace

import pytest

from financial_brain.forward_edge.prospective_cohort import (
    ProspectiveCohortError,
    confirm_champion,
    freeze_champion,
)
from financial_brain.forward_edge.prospective_registry import (
    ProspectiveCohortRegistry,
    ProspectiveRegistryError,
)


def _plan(**overrides):
    args = dict(
        development_returns={"candidate-a": [0.01, 0.02], "candidate-b": [0.03, 0.01]},
        development_sessions=["2026-01-05", "2026-01-06"],
        confirmation_sessions=["2026-01-07", "2026-01-08"],
        frozen_at=datetime(2026, 1, 6, 15, tzinfo=timezone.utc),
        first_confirmation_observation_at=datetime(2026, 1, 7, 3, tzinfo=timezone.utc),
    )
    args.update(overrides)
    return freeze_champion(**args)


def test_selection_is_development_only_and_plan_hash_is_reproducible():
    plan = _plan()
    assert plan.champion_id == "candidate-b"
    assert plan == _plan()
    assert len(plan.plan_sha256) == 64
    assert set(plan.development_sessions).isdisjoint(plan.confirmation_sessions)


def test_confirmation_never_promotes_on_partial_scheduled_data():
    result = confirm_champion(_plan(), confirmation_returns={"2026-01-07": 0.02})
    assert result.scheduled_sessions == 2
    assert result.observed_sessions == 1
    assert result.mean_net_return is None
    assert result.missing_sessions == ("2026-01-08",)


def test_complete_confirmation_reports_the_frozen_candidate_only():
    result = confirm_champion(
        _plan(), confirmation_returns={"2026-01-07": 0.01, "2026-01-08": -0.01}
    )
    assert result.champion_id == "candidate-b"
    assert result.mean_net_return == pytest.approx(0.0)
    assert result.missing_sessions == ()


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"confirmation_sessions": ["2026-01-06", "2026-01-08"]}, "must not overlap"),
        ({"confirmation_sessions": ["2026-01-04", "2026-01-08"]}, "must follow"),
        ({"frozen_at": datetime(2026, 1, 7, 4, tzinfo=timezone.utc)}, "before confirmation"),
        ({"first_confirmation_observation_at": datetime(2026, 1, 7, 3)}, "timezone-aware"),
        ({"development_returns": {"candidate-a": [0.01]}}, "one return per"),
    ],
)
def test_invalid_or_contaminated_selection_plan_is_refused(overrides, message):
    with pytest.raises(ProspectiveCohortError, match=message):
        _plan(**overrides)


def test_confirmation_refuses_unscheduled_or_nonfinite_returns():
    with pytest.raises(ProspectiveCohortError, match="unscheduled"):
        confirm_champion(_plan(), confirmation_returns={"2026-01-09": 0.1})
    with pytest.raises(ProspectiveCohortError, match="finite"):
        confirm_champion(_plan(), confirmation_returns={"2026-01-07": float("nan")})


def test_durable_registry_recovers_frozen_plan_and_immutable_session_receipt(tmp_path):
    path = tmp_path / "cohorts.jsonl"
    plan = _plan()
    registry = ProspectiveCohortRegistry(path)
    assert registry.register("main-champion", plan) == plan.plan_sha256
    sequence_after_register = registry.journal.seq
    assert registry.register("main-champion", plan) == plan.plan_sha256
    assert registry.journal.seq == sequence_after_register
    receipt = registry.record_observation(
        cohort_id="main-champion", session="2026-01-07",
        observed_at=datetime(2026, 1, 7, 3, 1, tzinfo=timezone.utc),
        outcome_sha256="a" * 64,
    )
    assert receipt["outcome_sha256"] == "a" * 64
    assert registry.record_observation(
        cohort_id="main-champion", session="2026-01-07",
        observed_at=datetime(2026, 1, 7, 3, 1, tzinfo=timezone.utc),
        outcome_sha256="a" * 64,
    ) == receipt
    assert registry.journal.seq == sequence_after_register + 1

    recovered = ProspectiveCohortRegistry(path)
    assert recovered.resolve("main-champion") == plan
    assert recovered.observations("main-champion") == (receipt,)


def test_durable_registry_refuses_plan_replacement_and_conflicting_observations(tmp_path):
    path = tmp_path / "cohorts.jsonl"
    plan = _plan()
    registry = ProspectiveCohortRegistry(path)
    registry.register("main-champion", plan)
    registry.record_observation(
        cohort_id="main-champion", session="2026-01-07",
        observed_at=datetime(2026, 1, 7, 3, 1, tzinfo=timezone.utc),
        outcome_sha256="a" * 64,
    )
    changed = _plan(development_returns={"candidate-a": [0.04, 0.04],
                                        "candidate-b": [0.01, 0.01]})
    with pytest.raises(ProspectiveRegistryError, match="immutable"):
        registry.register("main-champion", changed)
    with pytest.raises(ProspectiveRegistryError, match="immutable"):
        registry.record_observation(
            cohort_id="main-champion", session="2026-01-07",
            observed_at=datetime(2026, 1, 7, 3, 2, tzinfo=timezone.utc),
            outcome_sha256="b" * 64,
        )
    assert registry.observations("main-champion")[0]["outcome_sha256"] == "a" * 64


@pytest.mark.parametrize(
    "session, observed_at, digest, message",
    [
        ("2026-01-09", datetime(2026, 1, 9, tzinfo=timezone.utc), "a" * 64, "outside"),
        ("2026-01-07", datetime(2026, 1, 7, 2, tzinfo=timezone.utc), "a" * 64, "predates"),
        ("2026-01-07", datetime(2026, 1, 7, 4, tzinfo=timezone.utc), "bad", "SHA-256"),
    ],
)
def test_durable_registry_rejects_invalid_observation_receipts(
        tmp_path, session, observed_at, digest, message):
    registry = ProspectiveCohortRegistry(tmp_path / "cohorts.jsonl")
    registry.register("main-champion", _plan())
    with pytest.raises(ProspectiveRegistryError, match=message):
        registry.record_observation(cohort_id="main-champion", session=session,
                                    observed_at=observed_at, outcome_sha256=digest)


def test_durable_registry_rejects_forged_cohort_plan(tmp_path):
    registry = ProspectiveCohortRegistry(tmp_path / "cohorts.jsonl")
    with pytest.raises(ProspectiveRegistryError, match="digest does not match"):
        registry.register("main-champion", replace(_plan(), plan_sha256="0" * 64))
