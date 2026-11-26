from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.research.inference_registration import (
    InferenceDisposition, InferenceMethod, InferencePlan, InferencePlanRegistry,
    InferenceRegistrationError, InferenceUse,
)


def _plan(method: InferenceMethod = InferenceMethod.WHITE_REALITY_CHECK, registered_day: int = 1) -> InferencePlan:
    return InferencePlan("plan-1", method, ("A", "B"), 0.05,
                         datetime(2026, 1, registered_day, tzinfo=timezone.utc), "a" * 64,
                         block_length=5.0 if method is InferenceMethod.WHITE_REALITY_CHECK else None,
                         bootstrap_resamples=500 if method is InferenceMethod.WHITE_REALITY_CHECK else None)


def test_pre_registered_method_family_alpha_and_block_design_can_certify_forward() -> None:
    plan = _plan()
    use = InferenceUse(plan, datetime(2026, 1, 2, tzinfo=timezone.utc),
                       datetime(2026, 1, 3, tzinfo=timezone.utc))
    assert use.disposition is InferenceDisposition.PRE_REGISTERED
    assert use.may_certify_forward
    assert plan.sha256()


@pytest.mark.parametrize("registered_day", [2, 3])
def test_same_day_or_post_result_method_choice_is_diagnostic_only(registered_day: int) -> None:
    use = InferenceUse(_plan(registered_day=registered_day), datetime(2026, 1, 2, tzinfo=timezone.utc),
                       datetime(2026, 1, 4, tzinfo=timezone.utc))
    assert use.disposition is InferenceDisposition.RETROSPECTIVE_DIAGNOSTIC_ONLY
    assert not use.may_certify_forward


def test_family_level_method_rejects_single_candidate() -> None:
    with pytest.raises(InferenceRegistrationError, match="at least two"):
        InferencePlan("p", InferenceMethod.HANSEN_SPA, ("A",), 0.05,
                      datetime(2026, 1, 1, tzinfo=timezone.utc), "b" * 64,
                      block_length=3.0, bootstrap_resamples=200)
    with pytest.raises(InferenceRegistrationError, match="at least two"):
        InferencePlan("p", InferenceMethod.DEFLATED_SHARPE, ("A",), 0.05,
                      datetime(2026, 1, 1, tzinfo=timezone.utc), "b" * 64,
                      sharpe_variance=0.01)


def test_deflated_sharpe_requires_frozen_positive_null_variance() -> None:
    with pytest.raises(InferenceRegistrationError, match="frozen positive Sharpe variance"):
        InferencePlan("p", InferenceMethod.DEFLATED_SHARPE, ("A", "B"), 0.05,
                      datetime(2026, 1, 1, tzinfo=timezone.utc), "b" * 64)
    with pytest.raises(InferenceRegistrationError, match="frozen positive Sharpe variance"):
        InferencePlan("p", InferenceMethod.DEFLATED_SHARPE, ("A", "B"), 0.05,
                      datetime(2026, 1, 1, tzinfo=timezone.utc), "b" * 64,
                      sharpe_variance=float("nan"))
    with pytest.raises(InferenceRegistrationError, match="not applicable"):
        InferencePlan("p", InferenceMethod.HOLM, ("A", "B"), 0.05,
                      datetime(2026, 1, 1, tzinfo=timezone.utc), "b" * 64,
                      sharpe_variance=0.01)


def test_bootstrap_parameters_must_be_frozen_and_method_consistent() -> None:
    with pytest.raises(InferenceRegistrationError, match="fixed block length"):
        InferencePlan("p", InferenceMethod.WHITE_REALITY_CHECK, ("A", "B"), 0.05,
                      datetime(2026, 1, 1, tzinfo=timezone.utc), "b" * 64)
    with pytest.raises(InferenceRegistrationError, match="not applicable"):
        InferencePlan("p", InferenceMethod.HOLM, ("A", "B"), 0.05,
                      datetime(2026, 1, 1, tzinfo=timezone.utc), "b" * 64, block_length=3.0)


def test_candidate_family_order_and_identity_are_frozen() -> None:
    with pytest.raises(InferenceRegistrationError, match="sorted"):
        InferencePlan("p", InferenceMethod.HOLM, ("B", "A"), 0.05,
                      datetime(2026, 1, 1, tzinfo=timezone.utc), "b" * 64)


class FrozenClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


def test_registry_timestamps_plan_and_binds_one_forward_result(tmp_path) -> None:
    clock = FrozenClock(datetime(2026, 6, 1, tzinfo=timezone.utc))
    path = tmp_path / "inference.sqlite"
    registry = InferencePlanRegistry(path, clock=clock)
    plan = registry.register(method=InferenceMethod.WHITE_REALITY_CHECK, candidate_ids=("A", "B"),
                             alpha=0.05, preregistration_sha256="a" * 64,
                             block_length=5, bootstrap_resamples=500)
    clock.value += timedelta(days=1)
    registry.record_outcome_access(plan.plan_id, dataset_sha256="b" * 64,
                                   outcome_available_at=clock.value)
    clock.value += timedelta(days=1)
    use = registry.record_result(plan.plan_id, result_sha256="c" * 64)
    assert use.may_certify_forward
    with pytest.raises(InferenceRegistrationError, match="only one"):
        registry.record_result(plan.plan_id, result_sha256="d" * 64)
    assert registry.audit()["integrity"] == "VALID"
    registry.close()
    reopened = InferencePlanRegistry(path, clock=clock)
    assert reopened.audit()["events"] == 3
    reopened.close()


def test_registry_does_not_certify_historical_data_registered_today(tmp_path) -> None:
    clock = FrozenClock(datetime(2026, 7, 1, tzinfo=timezone.utc))
    registry = InferencePlanRegistry(tmp_path / "inference.sqlite", clock=clock)
    plan = registry.register(method=InferenceMethod.HOLM, candidate_ids=("A", "B"), alpha=0.05,
                             preregistration_sha256="a" * 64)
    clock.value += timedelta(days=1)
    registry.record_outcome_access(plan.plan_id, dataset_sha256="b" * 64,
                                   outcome_available_at=datetime(2020, 1, 1, tzinfo=timezone.utc))
    clock.value += timedelta(days=1)
    use = registry.record_result(plan.plan_id, result_sha256="c" * 64)
    assert use.disposition is InferenceDisposition.RETROSPECTIVE_DIAGNOSTIC_ONLY
    assert not use.may_certify_forward
    assert registry.audit()["integrity"] == "VALID"
    registry.close()


def test_registry_requires_access_before_scoring_and_rejects_repeat_access(tmp_path) -> None:
    clock = FrozenClock(datetime(2026, 8, 1, tzinfo=timezone.utc))
    registry = InferencePlanRegistry(tmp_path / "inference.sqlite", clock=clock)
    plan = registry.register(method=InferenceMethod.HOLM, candidate_ids=("A", "B"), alpha=0.05,
                             preregistration_sha256="a" * 64)
    with pytest.raises(InferenceRegistrationError, match="before outcome access"):
        registry.record_result(plan.plan_id, result_sha256="c" * 64)
    clock.value += timedelta(days=1)
    registry.record_outcome_access(plan.plan_id, dataset_sha256="b" * 64,
                                   outcome_available_at=clock.value)
    with pytest.raises(InferenceRegistrationError, match="already recorded"):
        registry.record_outcome_access(plan.plan_id, dataset_sha256="b" * 64,
                                       outcome_available_at=clock.value)
    registry.close()
