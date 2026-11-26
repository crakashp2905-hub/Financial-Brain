from datetime import datetime, timezone

import pytest

from financial_brain.research.inference_registration import (
    InferenceDisposition, InferenceMethod, InferencePlan, InferenceRegistrationError, InferenceUse,
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
