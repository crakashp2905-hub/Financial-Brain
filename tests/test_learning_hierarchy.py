from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.learning.hierarchy import (
    ActionKind,
    HierarchyDecision,
    HierarchyDecisionChain,
    HierarchyError,
    PolicyAction,
    available_global_actions,
)


NOW = datetime(2026, 11, 24, 9, 15, tzinfo=timezone.utc)


def test_safe_actions_are_available_without_any_alpha_candidates():
    actions = available_global_actions(alpha_candidate_ids=(), open_position_ids=("position-1",))
    assert {action.kind for action in actions} == {ActionKind.CASH, ActionKind.WAIT, ActionKind.CLOSE}
    assert all(action.candidate_independent for action in actions)


def test_global_safe_actions_remain_when_alpha_candidates_exist():
    actions = available_global_actions(alpha_candidate_ids=("NSE:INFY",), open_position_ids=())
    assert [(action.kind, action.target_id) for action in actions] == [
        (ActionKind.CASH, None), (ActionKind.WAIT, None), (ActionKind.OPEN, "NSE:INFY"),
    ]


def _chain(*, risk_allowed=True, projected=None):
    rows = []
    parent = None
    for i, level in enumerate(("GLOBAL", "DESK", "FAMILY", "INSTRUMENT", "EXECUTION")):
        row = HierarchyDecision(
            f"decision-{i}", "session-1", level, parent,
            PolicyAction(ActionKind.WAIT, candidate_independent=True), NOW + timedelta(seconds=i),
        )
        rows.append(row)
        parent = row.decision_id
    return HierarchyDecisionChain(
        tuple(rows), "a" * 64, "b" * 64,
        projected or PolicyAction(ActionKind.OPEN, "NSE:TCS"), risk_allowed,
    )


def test_chain_hash_pins_versions_order_links_and_risk_final_action():
    chain = _chain()
    assert len(chain.chain_sha256) == 64
    rejected = _chain(risk_allowed=False,
                      projected=PolicyAction(ActionKind.CASH, candidate_independent=True))
    assert len(rejected.chain_sha256) == 64


def test_rejected_risk_projection_cannot_leave_an_open_action():
    with pytest.raises(HierarchyError, match="risk rejection"):
        _chain(risk_allowed=False)


def test_hierarchy_rejects_broken_parent_chain():
    chain = _chain()
    broken = list(chain.decisions)
    broken[3] = HierarchyDecision("decision-3", "session-1", "INSTRUMENT", "wrong-parent",
                                  broken[3].proposed_action, broken[3].occurred_at_utc)
    with pytest.raises(HierarchyError, match="immediate parent"):
        HierarchyDecisionChain(tuple(broken), "a" * 64, "b" * 64,
                               PolicyAction(ActionKind.CASH, candidate_independent=True), False)


def test_invalid_candidate_independence_claim_is_rejected():
    with pytest.raises(HierarchyError, match="candidate-independent"):
        PolicyAction(ActionKind.OPEN, "NSE:INFY", candidate_independent=True)
