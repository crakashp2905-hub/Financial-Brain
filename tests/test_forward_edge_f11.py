"""F11 - model governance (FE-0124 .. FE-0131). Offline."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from financial_brain.forward_edge import governance as G
from financial_brain.forward_edge.approvals import Approval

NOW = datetime(2027, 3, 1, 5, 0, tzinfo=timezone.utc)
SHA = "f" * 64


def test_labels_unlock_only_after_exit_plus_embargo() -> None:
    q = G.MaturationQueue(timedelta(days=2))
    q.add("a", NOW - timedelta(days=5))
    q.add("b", NOW - timedelta(days=1))      # exited yesterday, still embargoed
    q.add("c", NOW + timedelta(days=3))      # not even exited
    assert q.mature(NOW) == ["a"]
    assert q.mature(NOW + timedelta(days=2)) == ["a", "b"]
    with pytest.raises(G.GovernanceError):
        q.add("a", NOW)
    with pytest.raises(G.GovernanceError):
        q.add("d", datetime(2027, 1, 1))


def test_model_id_reproduces_and_changes_with_any_input() -> None:
    kw = dict(data_sha256="1" * 64, feature_schema_sha256="2" * 64, params={"depth": "3"}, seed=7, code_sha256="3" * 64)
    assert G.model_id(**kw) == G.model_id(**kw)
    assert G.model_id(**{**kw, "seed": 8}) != G.model_id(**kw)
    assert G.model_id(**{**kw, "params": {"depth": "4"}}) != G.model_id(**kw)


def test_a_new_hyperparameter_search_cannot_touch_a_locked_cohort() -> None:
    locked = {date(2027, 2, 1), date(2027, 2, 2)}
    G.assert_search_data_clear({date(2026, 12, 1)}, locked)
    with pytest.raises(G.GovernanceError, match="locked cohort"):
        G.assert_search_data_clear({date(2026, 12, 1), date(2027, 2, 2)}, locked)


def test_challenger_must_beat_the_champion_after_the_multiplicity_penalty() -> None:
    assert G.challenger_beats_champion(mean_diff_net=0.0015, se_diff=0.0005, candidates_tested=1) is True
    assert G.challenger_beats_champion(mean_diff_net=0.0015, se_diff=0.0005, candidates_tested=50) is False
    with pytest.raises(G.GovernanceError):
        G.challenger_beats_champion(mean_diff_net=0.1, se_diff=0.0, candidates_tested=1)


def test_promotion_needs_preauthorization_status_and_trusted_receipt_coverage() -> None:
    assert G.promotion_allowed(status="VERIFIED", preauthorized=True, verified_sessions=80) == (True, "OK")
    assert G.promotion_allowed(status="VERIFIED", preauthorized=False, verified_sessions=80) == (False, "NOT_PREAUTHORIZED")
    assert G.promotion_allowed(status="SHADOW_ONLY", preauthorized=True, verified_sessions=80) == (False, "STATUS_NOT_PROMOTABLE")
    assert G.promotion_allowed(status="VERIFIED", preauthorized=True, verified_sessions=0) == (False, "NO_TRUSTED_RECEIPT_COVERAGE")


def _approval(kind="CAPITAL", actor="owner:akash", typ="human") -> Approval:
    return Approval(kind, "pilot", SHA, actor, typ, NOW - timedelta(hours=1), NOW + timedelta(hours=1))


def test_changing_capital_risk_or_leverage_needs_a_human_and_agents_cannot() -> None:
    G.authorize_economic_change("CAPITAL", "pilot", SHA, _approval(), NOW)
    with pytest.raises(G.GovernanceError, match="cannot approve"):
        G.authorize_economic_change("CAPITAL", "pilot", SHA, _approval(actor="agent:allocator", typ="agent"), NOW)
    with pytest.raises(G.GovernanceError, match="required"):
        G.authorize_economic_change("LEVERAGE", "pilot", SHA, None, NOW)
    with pytest.raises(G.GovernanceError, match="not an economic"):
        G.authorize_economic_change("STRATEGY_ENROLLMENT", "pilot", SHA, _approval(), NOW)


def test_transitions_are_logged_and_never_deleted() -> None:
    log = G.TransitionLog()
    log.record("DRIFT", "M1", "psi 0.4", NOW)
    log.record("ROLLBACK", "M1", "to frozen v1", NOW)
    assert [r["kind"] for r in log.rows()] == ["DRIFT", "ROLLBACK"]
    with pytest.raises(G.GovernanceError, match="never deleted"):
        log.delete(0)
    with pytest.raises(G.GovernanceError):
        log.record("WHIM", "M1", "x", NOW)


@pytest.mark.parametrize("event", ["DRIFT", "OUTAGE", "ACCOUNTING_MISMATCH"])
def test_drift_outage_or_mismatch_reverts_to_the_frozen_safe_policy_with_no_new_orders(event: str) -> None:
    g = G.SafetyGovernor()
    g.observe(event, open_orders=3)
    assert g.active_policy == "FROZEN_SAFE" and g.new_orders_allowed is False and g.cancelled_open_orders == 3


def test_benign_events_do_not_change_the_policy() -> None:
    g = G.SafetyGovernor()
    g.observe("HEARTBEAT")
    assert g.active_policy == "CANDIDATE" and g.new_orders_allowed is True
