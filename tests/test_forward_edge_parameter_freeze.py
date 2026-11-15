from datetime import date, datetime, timezone

import pytest

from financial_brain.forward_edge.parameter_freeze import (
    ParameterFreezeError,
    StrategyFreeze,
    StrategyFreezeRegistry,
)


def _freeze(cohort="cohort-1", params="params-v1"):
    return StrategyFreeze(cohort, "trial-1", "momentum", "code-v1", params, "model-v1",
                          datetime(2026, 11, 15, 12, tzinfo=timezone.utc), date(2026, 11, 16))


def test_exact_frozen_strategy_is_allowed_and_parameter_changes_are_refused(tmp_path) -> None:
    registry = StrategyFreezeRegistry(tmp_path / "freeze.sqlite3")
    frozen = _freeze()
    assert registry.freeze(frozen)
    assert not registry.freeze(frozen)
    digest = registry.validate_run(cohort_id="cohort-1", session=date(2026, 11, 16),
                                   strategy_id="momentum", code_hash="code-v1",
                                   parameters_hash="params-v1", model_hash="model-v1")
    assert digest == frozen.digest()
    with pytest.raises(ParameterFreezeError, match="changed"):
        registry.validate_run(cohort_id="cohort-1", session=date(2026, 11, 17),
                              strategy_id="momentum", code_hash="code-v1",
                              parameters_hash="tuned-after-outcomes", model_hash="model-v1")
    registry.close()


def test_unknown_or_pre_cohort_runs_fail_closed(tmp_path) -> None:
    registry = StrategyFreezeRegistry(tmp_path / "freeze.sqlite3")
    registry.freeze(_freeze())
    with pytest.raises(ParameterFreezeError, match="unknown cohort"):
        registry.validate_run(cohort_id="other", session=date(2026, 11, 16), strategy_id="momentum",
                              code_hash="code-v1", parameters_hash="params-v1", model_hash="model-v1")
    with pytest.raises(ParameterFreezeError, match="precedes"):
        registry.validate_run(cohort_id="cohort-1", session=date(2026, 11, 15), strategy_id="momentum",
                              code_hash="code-v1", parameters_hash="params-v1", model_hash="model-v1")
    registry.close()


def test_cohort_binding_cannot_be_overwritten(tmp_path) -> None:
    registry = StrategyFreezeRegistry(tmp_path / "freeze.sqlite3")
    registry.freeze(_freeze())
    with pytest.raises(ParameterFreezeError, match="new governed trial"):
        registry.freeze(_freeze(params="changed"))
    registry.close()
