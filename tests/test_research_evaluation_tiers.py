from __future__ import annotations

import pytest

from financial_brain.research.evaluation_tiers import (
    EvaluationTier, ResearchAction, TierAccessError, TierRequest, authorize,
)


@pytest.mark.parametrize("action", [ResearchAction.INSPECT, ResearchAction.TUNE])
def test_exploratory_screening_can_inspect_and_tune(action: ResearchAction) -> None:
    authorize(TierRequest(EvaluationTier.EXPLORATORY_SCREENING, action, "screen-1"))


@pytest.mark.parametrize("tier,action", [
    (EvaluationTier.EXPLORATORY_SCREENING, ResearchAction.PROMOTE),
    (EvaluationTier.EXPLORATORY_SCREENING, ResearchAction.VALIDATE_FROZEN),
    (EvaluationTier.PREREGISTERED_VALIDATION, ResearchAction.TUNE),
    (EvaluationTier.PREREGISTERED_VALIDATION, ResearchAction.OPEN_HOLDOUT),
    (EvaluationTier.SINGLE_USE_HOLDOUT, ResearchAction.TUNE),
    (EvaluationTier.FUTURE_OBSERVATION, ResearchAction.TUNE),
    (EvaluationTier.FUTURE_OBSERVATION, ResearchAction.PROMOTE),
])
def test_tier_forbids_cross_stage_actions(tier: EvaluationTier, action: ResearchAction) -> None:
    req = TierRequest(tier, action, "run-1", "a" * 64)
    with pytest.raises(TierAccessError):
        authorize(req)


def test_validation_and_holdout_require_frozen_preregistration_identity() -> None:
    for tier, action in [
        (EvaluationTier.PREREGISTERED_VALIDATION, ResearchAction.VALIDATE_FROZEN),
        (EvaluationTier.SINGLE_USE_HOLDOUT, ResearchAction.OPEN_HOLDOUT),
    ]:
        with pytest.raises(TierAccessError, match="preregistration hash"):
            authorize(TierRequest(tier, action, "run-1"))


def test_unknown_action_cannot_be_smuggled_as_a_string() -> None:
    with pytest.raises(TierAccessError):
        TierRequest(EvaluationTier.EXPLORATORY_SCREENING, "OPEN_HOLDOUT", "run-1")  # type: ignore[arg-type]
