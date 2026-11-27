from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.portfolio.allocator_benchmarks import (
    AllocationBenchmarkError,
    AllocationEvaluationSession,
    AllocationMethod,
    FrozenAllocationPolicy,
    compare_allocation_methods,
)

START = datetime(2026, 10, 1, 9, 15, tzinfo=timezone.utc)
TRAIN = "a" * 64
EVALUATION = "b" * 64


def _policies(*, frozen_at=START - timedelta(days=2), train_hash=TRAIN):
    pairs = (
        (AllocationMethod.EQUAL_RISK, {"A": 0.50, "B": 0.50}),
        (AllocationMethod.EQUAL_WEIGHT, {"A": 0.50, "B": 0.50}),
        (AllocationMethod.VOLATILITY_TARGETED, {"A": 0.70, "B": 0.20}),
        (AllocationMethod.ROBUST_MEAN_VARIANCE, {"A": 0.65, "B": 0.25}),
        (AllocationMethod.REINFORCEMENT_LEARNING, {"A": 0.30, "B": 0.60}),
    )
    return tuple(FrozenAllocationPolicy(method, "v1", frozen_at, frozen_at,
                                        train_hash, f"{index + 1:x}" * 64, weights)
                 for index, (method, weights) in enumerate(pairs))


def _sessions():
    return (
        AllocationEvaluationSession("s1", START, {"A": 0.02, "B": -0.01}, 0.001),
        AllocationEvaluationSession("s2", START + timedelta(days=1), {"A": -0.03, "B": 0.04}, 0.001),
        AllocationEvaluationSession("s3", START + timedelta(days=2), {"A": 0.01, "B": 0.01}, 0.001),
    )


def test_all_methods_are_frozen_and_scored_on_the_same_net_session_panel():
    result = compare_allocation_methods(policies=_policies(), sessions=_sessions(),
                                       evaluation_data_sha256=EVALUATION)
    assert set(result.method_metrics) == {method.value for method in AllocationMethod}
    assert result.evaluation_sessions == 3
    assert result.method_metrics["EQUAL_WEIGHT"]["cash_weight"] == 0.0
    assert result.method_metrics["VOLATILITY_TARGETED"]["cash_weight"] == pytest.approx(0.1)
    assert all(metric["session_return_sha256"] for metric in result.method_metrics.values())
    assert not result.promotion_allowed
    assert result == compare_allocation_methods(policies=_policies(), sessions=_sessions(),
                                                evaluation_data_sha256=EVALUATION)


def test_missing_method_or_mismatched_training_snapshot_is_refused():
    with pytest.raises(AllocationBenchmarkError, match="all five methods"):
        compare_allocation_methods(policies=_policies()[:-1], sessions=_sessions(),
                                   evaluation_data_sha256=EVALUATION)
    policies = list(_policies())
    policies[-1] = FrozenAllocationPolicy(AllocationMethod.REINFORCEMENT_LEARNING, "v1",
                                          START - timedelta(days=2), START - timedelta(days=2),
                                          "c" * 64, "e" * 64, {"A": 0.3, "B": 0.6})
    with pytest.raises(AllocationBenchmarkError, match="same training-data"):
        compare_allocation_methods(policies=tuple(policies), sessions=_sessions(),
                                   evaluation_data_sha256=EVALUATION)


def test_no_policy_can_be_frozen_after_evaluation_starts_or_exceed_capital():
    with pytest.raises(AllocationBenchmarkError, match="frozen before"):
        compare_allocation_methods(policies=_policies(frozen_at=START), sessions=_sessions(),
                                   evaluation_data_sha256=EVALUATION)
    with pytest.raises(AllocationBenchmarkError, match="cannot exceed full capital"):
        FrozenAllocationPolicy(AllocationMethod.EQUAL_WEIGHT, "v1", START - timedelta(days=2),
                              START - timedelta(days=2), TRAIN, "f" * 64, {"A": 0.8, "B": 0.4})
