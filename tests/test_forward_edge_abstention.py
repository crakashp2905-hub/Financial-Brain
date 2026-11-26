from datetime import date, datetime, timezone

import pytest

from financial_brain.forward_edge.abstention import (
    AbstentionError,
    AbstentionOutcome,
    ExecutionDisposition,
    FrozenAbstentionPolicy,
    evaluate_abstentions,
)


def _policy():
    return FrozenAbstentionPolicy("risk-filter-v1", "strategy-sha", "params-sha",
                                  datetime(2026, 11, 15, 10, tzinfo=timezone.utc),
                                  date(2026, 11, 16))


def test_all_opportunities_count_and_abstention_missed_winners_are_reported() -> None:
    policy = _policy()
    d = date(2026, 11, 16)
    ph = policy.digest()
    outcomes = (
        AbstentionOutcome("o1", d, policy.policy_id, ph, "TRADE", "RESOLVED", 0.012, 0.010,
                          execution_disposition=ExecutionDisposition.FILLED, fill_fraction=1.0),
        AbstentionOutcome("o2", d, policy.policy_id, ph, "ABSTAIN", "RESOLVED", 0.0, 0.025, "risk_limit",
                          ExecutionDisposition.NO_ORDER),
        AbstentionOutcome("o3", d, policy.policy_id, ph, "ABSTAIN", "RESOLVED", 0.0, -0.03, "stale_quote",
                          ExecutionDisposition.REJECTED),
        AbstentionOutcome("o4", d, policy.policy_id, ph, "ABSTAIN", "UNRESOLVED", None, None, "missing_mark",
                          ExecutionDisposition.NO_ORDER),
    )
    report = evaluate_abstentions(policy, ("o1", "o2", "o3", "o4"), outcomes)
    assert report.total_opportunities == report.traded + report.abstained == 4
    assert report.missed_winners == 1
    assert report.abstention_avoided_losses == 1
    assert report.unresolved == report.unresolved_abstentions == 1
    assert report.realized_policy_net_return_sum == 0.012
    assert report.execution_counts == (("NO_ORDER", 2), ("REJECTED", 1), ("DELAYED", 0),
                                       ("NO_FILL", 0), ("PARTIAL_FILL", 0), ("FILLED", 1),
                                       ("UNKNOWN", 0))
    assert report.missed_execution_winners == 1
    assert report.execution_avoided_losses == 1


def test_missing_opportunity_or_changed_policy_cannot_be_silently_dropped() -> None:
    policy = _policy()
    outcome = AbstentionOutcome("o1", date(2026, 11, 16), policy.policy_id, policy.digest(),
                                "ABSTAIN", "RESOLVED", 0.0, 0.01, "filter", ExecutionDisposition.NO_ORDER)
    with pytest.raises(AbstentionError, match="exactly match"):
        evaluate_abstentions(policy, ("o1", "o2"), (outcome,))
    changed = AbstentionOutcome("o1", date(2026, 11, 16), policy.policy_id, "other-hash",
                                "ABSTAIN", "RESOLVED", 0.0, 0.01, "filter", ExecutionDisposition.NO_ORDER)
    with pytest.raises(AbstentionError, match="lineage"):
        evaluate_abstentions(policy, ("o1",), (changed,))


def test_abstentions_need_reasons_and_frozen_policy_precedes_cohort() -> None:
    with pytest.raises(AbstentionError, match="before"):
        FrozenAbstentionPolicy("p", "s", "v", datetime(2026, 11, 16, tzinfo=timezone.utc),
                               date(2026, 11, 16))
    policy = _policy()
    with pytest.raises(AbstentionError, match="reason"):
        AbstentionOutcome("o", date(2026, 11, 16), policy.policy_id, policy.digest(),
                          "ABSTAIN", "RESOLVED", 0.0, 0.01, execution_disposition=ExecutionDisposition.NO_ORDER)


@pytest.mark.parametrize("state,fill", [
    (ExecutionDisposition.FILLED, 0.5),
    (ExecutionDisposition.PARTIAL_FILL, 1.0),
    (ExecutionDisposition.NO_FILL, 0.25),
    (ExecutionDisposition.UNKNOWN, 0.0),
])
def test_execution_disposition_must_match_observed_fill(state, fill) -> None:
    policy = _policy()
    with pytest.raises(AbstentionError):
        AbstentionOutcome("o", date(2026, 11, 16), policy.policy_id, policy.digest(),
                          "TRADE", "UNRESOLVED" if state is ExecutionDisposition.UNKNOWN else "RESOLVED",
                          None if state is ExecutionDisposition.UNKNOWN else 0.0,
                          None if state is ExecutionDisposition.UNKNOWN else 0.0,
                          execution_disposition=state, fill_fraction=fill)
