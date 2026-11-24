from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.learning.execution_reward import (
    ExecutionFill,
    ExecutionRewardError,
    ParentOrder,
    score_execution,
)

NOW = datetime(2026, 11, 24, 9, 15, tzinfo=timezone.utc)


def parent(side: str = "BUY", quantity: int = 100) -> ParentOrder:
    return ParentOrder("parent-1", side, quantity, 100.0, NOW, NOW + timedelta(minutes=5), 0.25)  # type: ignore[arg-type]


def test_buy_shortfall_fees_and_nonfill_penalty_are_separate_and_labeled() -> None:
    result = score_execution(parent(), (
        ExecutionFill("fill-1", 60, 101.0, 2.0, NOW + timedelta(minutes=1), "REPLAYED_OBSERVED"),  # type: ignore[arg-type]
    ))
    assert result["implementation_shortfall_inr"] == 60.0
    assert result["fees_inr"] == 2.0
    assert result["alpha_decay_non_fill_penalty_inr"] == 10.0
    assert result["total_execution_cost_inr"] == 72.0
    assert result["reward_inr"] == -72.0
    assert result["evidence_label"] == "REPLAYED_OBSERVED"
    assert result["parent_approval_immutable"] is True


def test_noop_is_penalized_and_cannot_claim_a_fill_label() -> None:
    result = score_execution(parent(), ())
    assert result["filled_quantity"] == 0
    assert result["alpha_decay_non_fill_penalty_inr"] == 25.0
    assert result["evidence_label"] == "NO_FILL"


def test_sell_shortfall_sign_rewards_better_execution() -> None:
    result = score_execution(parent("SELL", 10), (
        ExecutionFill("fill-1", 10, 101.0, 0.0, NOW + timedelta(minutes=1), "BROKER_EXECUTED"),  # type: ignore[arg-type]
    ))
    assert result["implementation_shortfall_inr"] == -10.0
    assert result["total_execution_cost_inr"] == -10.0
    assert result["evidence_label"] == "BROKER_EXECUTED"


@pytest.mark.parametrize("invalid", [
    (ExecutionFill("same", 60, 100.0, 0.0, NOW, "SIMULATED"),
     ExecutionFill("same", 40, 100.0, 0.0, NOW, "SIMULATED")),
    (ExecutionFill("too-many", 101, 100.0, 0.0, NOW, "SIMULATED"),),
    (ExecutionFill("late", 1, 100.0, 0.0, NOW + timedelta(minutes=6), "SIMULATED"),),
])
def test_duplicate_overfill_or_late_evidence_is_rejected(invalid: tuple[ExecutionFill, ...]) -> None:
    with pytest.raises(ExecutionRewardError):
        score_execution(parent(), invalid)


def test_missing_alpha_decay_cost_fails_closed() -> None:
    p = ParentOrder("p", "BUY", 1, 100.0, NOW, NOW + timedelta(minutes=1), 0.0)  # type: ignore[arg-type]
    with pytest.raises(ExecutionRewardError, match="alpha-decay"):
        score_execution(p, ())
