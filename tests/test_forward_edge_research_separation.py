from datetime import date, datetime, timezone

import pytest

from financial_brain.forward_edge.research_separation import (
    LockedCohortRegistry,
    LockedEvaluationCohort,
    ResearchPurpose,
    ResearchRun,
    ResearchSeparationError,
    authorize_research_run,
)


FROZEN = datetime(2026, 11, 16, 4, tzinfo=timezone.utc)
EVAL = (date(2026, 11, 17), date(2026, 11, 18))
MODEL, SPEC, DATA = "a" * 64, "b" * 64, "c" * 64


def _cohort():
    return LockedEvaluationCohort("q4-paper-1", FROZEN, EVAL, DATA, MODEL, SPEC)


def _run(purpose, **overrides):
    values = {"run_id": "run-1", "purpose": purpose,
              "artifact_namespace": purpose.value.lower(),
              "training_sessions": (date(2026, 11, 1),), "evaluation_sessions": (),
              "searched_hyperparameters": True, "model_sha256": MODEL,
              "strategy_spec_sha256": SPEC}
    values.update(overrides)
    return ResearchRun(**values)


def test_diagnostics_and_ablations_are_separate_from_locked_evaluation_data():
    for purpose in (ResearchPurpose.LEARNING_DIAGNOSTIC, ResearchPurpose.OFFLINE_ABLATION):
        allowed = authorize_research_run(_run(purpose), _cohort())
        assert allowed.allowed
        contaminated = authorize_research_run(
            _run(purpose, training_sessions=(EVAL[0],)), _cohort())
        assert not contaminated.allowed
        assert "LOCKED_COHORT_CONTAMINATION" in contaminated.reasons


def test_paper_challenger_must_be_frozen_and_use_asof_labels():
    valid = authorize_research_run(
        _run(ResearchPurpose.PAPER_CHALLENGER, searched_hyperparameters=False,
             evaluation_sessions=(EVAL[0],)), _cohort())
    assert valid.allowed
    tuned = authorize_research_run(
        _run(ResearchPurpose.PAPER_CHALLENGER, evaluation_sessions=(EVAL[0],),
             labels_used_at_decision=True), _cohort())
    assert not tuned.allowed
    assert {"PAPER_CHALLENGER_PARAMETERS_MUST_BE_FROZEN", "FORWARD_LABEL_LOOKAHEAD"} <= set(tuned.reasons)


def test_promotion_validation_requires_exact_frozen_model_spec_and_cohort_data():
    base = {"searched_hyperparameters": False, "evaluation_sessions": EVAL,
            "evaluation_data_sha256": DATA}
    assert authorize_research_run(_run(ResearchPurpose.PROMOTION_VALIDATION, **base),
                                 _cohort()).allowed
    wrong_data = authorize_research_run(
        _run(ResearchPurpose.PROMOTION_VALIDATION, **{**base, "evaluation_data_sha256": "d" * 64}),
        _cohort())
    assert "EVALUATION_DATA_DIFFERS_FROM_LOCKED_COHORT" in wrong_data.reasons
    tuned = authorize_research_run(
        _run(ResearchPurpose.PROMOTION_VALIDATION, **{**base, "searched_hyperparameters": True}),
        _cohort())
    assert "PROMOTION_VALIDATION_CANNOT_TUNE" in tuned.reasons


def test_namespaces_and_cohort_freezes_are_immutable(tmp_path):
    run = _run(ResearchPurpose.LEARNING_DIAGNOSTIC, artifact_namespace="paper_challenger")
    assert "ARTIFACT_NAMESPACE_MISMATCH" in authorize_research_run(run, _cohort()).reasons
    registry = LockedCohortRegistry(tmp_path / "cohorts.sqlite3")
    cohort = _cohort()
    assert registry.freeze(cohort)
    assert not registry.freeze(cohort)
    changed = LockedEvaluationCohort(cohort.cohort_id, cohort.frozen_at, EVAL,
                                     "d" * 64, MODEL, SPEC)
    with pytest.raises(ResearchSeparationError, match="immutable"):
        registry.freeze(changed)
    registry.close()


def test_evaluation_cohort_cannot_be_frozen_after_it_started():
    with pytest.raises(ResearchSeparationError, match="before its first session"):
        LockedEvaluationCohort("late", datetime(2026, 11, 17, tzinfo=timezone.utc),
                               (date(2026, 11, 17),), DATA, MODEL, SPEC)
