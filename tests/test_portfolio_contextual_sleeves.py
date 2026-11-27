from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.portfolio.contextual_sleeves import (
    ContextualAllocationError,
    ContextualSleeveAllocator,
    MaturedSleeveReward,
    SleeveOrderCandidate,
)

NOW = datetime(2026, 11, 25, 9, 30, tzinfo=timezone.utc)
CTX = {"regime": "TREND", "volatility": "LOW"}
HASH = "a" * 64


def _candidate(sleeve: str, *, context=CTX, eligible_at=NOW + timedelta(seconds=1)):
    return SleeveOrderCandidate(sleeve, "v1", f"NSE:{sleeve}", "BUY", 10, "0.05",
                                NOW - timedelta(seconds=1), eligible_at, context, True)


def _rewards(sleeve: str, count: int, reward: float, *, matured_at=NOW - timedelta(seconds=1),
             context=CTX, tag="base", cluster_prefix=None):
    return tuple(MaturedSleeveReward(f"{sleeve}-{tag}-{index}", sleeve,
                                     cluster_prefix or f"{tag}-{index}", context,
                                     NOW - timedelta(days=2), matured_at, reward, HASH)
                 for index in range(count))


def test_allocator_uses_only_matured_exact_context_net_rewards_and_freezes_intent():
    allocator = ContextualSleeveAllocator(minimum_matured_observations=3, maximum_sleeve_weight=0.6)
    rows = (_rewards("momentum", 3, 0.04, tag="primary")
            + _rewards("meanrev", 3, 0.02, tag="secondary")
            + _rewards("momentum", 1, 99, matured_at=NOW + timedelta(days=1), tag="future")
            + _rewards("momentum", 1, 99, context={"regime": "RANGE", "volatility": "LOW"}, tag="other"))
    result = allocator.allocate(observations=rows,
                                candidates=(_candidate("momentum"), _candidate("meanrev")),
                                context=CTX, as_of=NOW, cash_return_hurdle=0.01)
    assert result.weights == {"meanrev": pytest.approx(0.4), "momentum": pytest.approx(0.6)}
    assert result.cash_weight == pytest.approx(0)
    assert result.matched_observation_counts == {"meanrev": 3, "momentum": 3}
    assert result.matched_cluster_counts == {"meanrev": 3, "momentum": 3}
    assert result.lower_confidence_bounds == {"meanrev": pytest.approx(0.02),
                                               "momentum": pytest.approx(0.04)}
    assert result.interval_method == "ONE_SIDED_STUDENT_T_ON_CONTEXT_MATCHED_CLUSTER_MEANS_IID"
    assert len(result.intents) == 2
    assert all(row.allocation_sha256 == result.allocation_sha256 for row in result.intents)
    assert all(row.context_sha256 == result.context_sha256 for row in result.intents)
    assert all(row.intent_sha256 != "" for row in result.intents)
    assert not result.promotion_allowed


def test_no_matured_support_or_insufficient_data_keeps_full_weight_in_cash():
    result = ContextualSleeveAllocator(minimum_matured_observations=3).allocate(
        observations=_rewards("momentum", 2, 0.5), candidates=(_candidate("momentum"),),
        context=CTX, as_of=NOW, cash_return_hurdle=0.0)
    assert result.weights == {}
    assert result.cash_weight == 1.0
    assert result.intents == ()


def test_reward_context_and_candidate_context_are_bound_and_future_rows_are_ignored():
    allocator = ContextualSleeveAllocator(minimum_matured_observations=1)
    with pytest.raises(ContextualAllocationError, match="exactly match"):
        allocator.allocate(observations=(), candidates=(_candidate("momentum", context={"regime": "RANGE"}),),
                           context=CTX, as_of=NOW, cash_return_hurdle=0.0)
    out = allocator.allocate(observations=_rewards("momentum", 1, 0.1,
                                                   matured_at=NOW + timedelta(seconds=1)),
                             candidates=(_candidate("momentum"),), context=CTX, as_of=NOW,
                             cash_return_hurdle=0.0)
    assert out.cash_weight == 1.0


def test_invalid_or_duplicate_reward_ids_and_unqualified_intents_fail_closed():
    allocator = ContextualSleeveAllocator(minimum_matured_observations=1)
    reward = _rewards("momentum", 1, 0.1)[0]
    with pytest.raises(ContextualAllocationError, match="unique"):
        allocator.allocate(observations=(reward, reward), candidates=(_candidate("momentum"),),
                           context=CTX, as_of=NOW, cash_return_hurdle=0.0)
    with pytest.raises(ContextualAllocationError, match="risk-qualified"):
        SleeveOrderCandidate("bad", "v1", "NSE:BAD", "BUY", 1, "0.05", NOW,
                             NOW + timedelta(seconds=1), CTX, False)


def test_lower_confidence_bound_keeps_capital_in_cash_under_weak_or_clustered_evidence():
    allocator = ContextualSleeveAllocator(minimum_matured_observations=3)
    candidate = (_candidate("momentum"),)
    noisy = allocator.allocate(observations=(
        MaturedSleeveReward("n1", "momentum", "d1", CTX, NOW - timedelta(days=3),
                            NOW - timedelta(days=1), 0.20, HASH),
        MaturedSleeveReward("n2", "momentum", "d2", CTX, NOW - timedelta(days=2),
                            NOW - timedelta(days=1), -0.10, HASH),
        MaturedSleeveReward("n3", "momentum", "d3", CTX, NOW - timedelta(days=1),
                            NOW - timedelta(seconds=1), 0.02, HASH),
    ), candidates=candidate, context=CTX, as_of=NOW, cash_return_hurdle=0.0)
    assert noisy.lower_confidence_bounds["momentum"] < 0
    assert noisy.weights == {}
    assert noisy.cash_weight == 1

    same_session = allocator.allocate(observations=_rewards(
        "momentum", 3, 0.10, tag="session", cluster_prefix="single-session"),
        candidates=candidate, context=CTX, as_of=NOW, cash_return_hurdle=0.0)
    assert same_session.matched_observation_counts["momentum"] == 3
    assert same_session.matched_cluster_counts["momentum"] == 1
    assert same_session.lower_confidence_bounds == {}
    assert same_session.cash_weight == 1



