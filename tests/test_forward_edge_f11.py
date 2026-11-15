"""F11 - model governance (FE-0124 .. FE-0131). Offline."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

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


def _approval(kind="CAPITAL", actor="owner:akash", typ="human", subject="pilot", scope=SHA) -> Approval:
    return Approval(kind, subject, scope, actor, typ, NOW - timedelta(hours=1), NOW + timedelta(hours=1))


def test_changing_capital_risk_or_leverage_needs_a_human_and_agents_cannot() -> None:
    with pytest.raises(G.GovernanceError, match="family evidence gate"):
        G.authorize_economic_change("CAPITAL", "pilot", SHA, _approval(), NOW)
    leverage_approval = _approval(kind="LEVERAGE")
    G.authorize_economic_change("LEVERAGE", "pilot", SHA, leverage_approval, NOW)
    with pytest.raises(G.GovernanceError, match="cannot approve"):
        G.authorize_economic_change("LEVERAGE", "pilot", SHA,
                                    _approval(kind="LEVERAGE", actor="agent:allocator", typ="agent"), NOW)
    with pytest.raises(G.GovernanceError, match="required"):
        G.authorize_economic_change("LEVERAGE", "pilot", SHA, None, NOW)
    with pytest.raises(G.GovernanceError, match="not an economic"):
        G.authorize_economic_change("STRATEGY_ENROLLMENT", "pilot", SHA, _approval(), NOW)


def _family_evidence(**overrides) -> G.FamilyEvidence:
    values = {
        "family_id": "family:momentum-v1", "status": "VERIFIED",
        "strategy_sha256": "1" * 64, "frozen_protocol_sha256": "2" * 64,
        "evidence_manifest_sha256": "3" * 64, "protocol_frozen": True,
        "protocol_frozen_at": NOW - timedelta(days=100),
        "cohort_started_at": NOW - timedelta(days=99), "verified_sessions": 80,
        "receipt_sessions": 80, "candidate_count": 12, "multiplicity_adjusted": True,
        "cost_stress_passed": True, "matched_control_lcb_bps": Decimal("1.2"),
        "net_alpha_lcb_bps": Decimal("0.8"),
    }
    values.update(overrides)
    return G.FamilyEvidence(**values)


def _family_approval(evidence, action, *, current="100000", requested="110000",
                     kind=None, actor="owner:akash", actor_type="human", scope=None):
    action_kind = kind or ("CAPITAL" if action == "CAPITAL_INCREASE" else "STRATEGY_ENROLLMENT")
    scoped = scope or G.family_action_scope_sha256(
        evidence=evidence, action=action,
        current_capital_inr=Decimal(current), requested_capital_inr=Decimal(requested),
        execution_mode="PAPER",
    )
    return Approval(action_kind, evidence.family_id, scoped, actor, actor_type,
                    NOW - timedelta(hours=1), NOW + timedelta(hours=1))


def test_family_gate_binds_capital_increase_to_costed_protocol_evidence_and_human_review():
    evidence = _family_evidence()
    approval = _family_approval(evidence, "CAPITAL_INCREASE")
    verdict = G.evaluate_family_action(
        evidence=evidence, action="CAPITAL_INCREASE", current_capital_inr=Decimal("100000"),
        requested_capital_inr=Decimal("110000"), owner_approval=approval, now=NOW,
    )
    assert verdict.approved is True
    assert verdict.reasons == ()
    assert verdict.scope_sha256 == approval.scope_sha256


def test_family_gate_requires_exact_owner_approval_and_never_authorizes_live():
    evidence = _family_evidence()
    missing = G.evaluate_family_action(
        evidence=evidence, action="CAPITAL_INCREASE", current_capital_inr=Decimal("100000"),
        requested_capital_inr=Decimal("110000"), owner_approval=None, now=NOW,
    )
    assert "SCOPED_HUMAN_OWNER_APPROVAL_REQUIRED" in missing.reasons

    agent = _family_approval(evidence, "CAPITAL_INCREASE", actor="agent:allocator", actor_type="agent")
    blocked_agent = G.evaluate_family_action(
        evidence=evidence, action="CAPITAL_INCREASE", current_capital_inr=Decimal("100000"),
        requested_capital_inr=Decimal("110000"), owner_approval=agent, now=NOW,
    )
    assert any(reason.startswith("OWNER_APPROVAL_FAILED:") for reason in blocked_agent.reasons)

    stale = _family_approval(evidence, "CAPITAL_INCREASE", scope="4" * 64)
    blocked_stale = G.evaluate_family_action(
        evidence=evidence, action="CAPITAL_INCREASE", current_capital_inr=Decimal("100000"),
        requested_capital_inr=Decimal("110000"), owner_approval=stale, now=NOW,
    )
    assert any("stale approval" in reason for reason in blocked_stale.reasons)

    paper_approval = _family_approval(evidence, "AUTO_EXECUTION_ENABLE", current="100000",
                                      requested="100000")
    live = G.evaluate_family_action(
        evidence=evidence, action="AUTO_EXECUTION_ENABLE", current_capital_inr=Decimal("100000"),
        requested_capital_inr=Decimal("100000"), owner_approval=paper_approval, now=NOW,
        execution_mode="LIVE",
    )
    assert live.approved is False
    assert "LIVE_MODE_REQUIRES_SEPARATE_RELEASE_GATE" in live.reasons


@pytest.mark.parametrize(("changes", "reason"), [
    ({"status": "SHADOW_ONLY"}, "FAMILY_STATUS_NOT_VERIFIED"),
    ({"protocol_frozen": False}, "FROZEN_PROTOCOL_NOT_ATTESTED_BEFORE_COHORT"),
    ({"protocol_frozen_at": NOW}, "FROZEN_PROTOCOL_NOT_ATTESTED_BEFORE_COHORT"),
    ({"verified_sessions": 59}, "INSUFFICIENT_FAMILY_VERIFIED_SESSIONS"),
    ({"receipt_sessions": 79}, "FAMILY_RECEIPT_COVERAGE_INCOMPLETE"),
    ({"candidate_count": 0}, "FAMILY_CANDIDATE_DENOMINATOR_MISSING"),
    ({"multiplicity_adjusted": False}, "FAMILY_MULTIPLICITY_NOT_ADJUSTED"),
    ({"cost_stress_passed": False}, "FAMILY_COST_STRESS_FAILED"),
    ({"matched_control_lcb_bps": Decimal("0")}, "FAMILY_MATCHED_CONTROL_NOT_POSITIVE"),
    ({"net_alpha_lcb_bps": Decimal("-0.1")}, "FAMILY_NET_ALPHA_NOT_POSITIVE"),
])
def test_each_family_evidence_failure_blocks_capital_and_automation(changes, reason):
    evidence = _family_evidence(**changes)
    for action in ("CAPITAL_INCREASE", "AUTO_EXECUTION_ENABLE"):
        current, requested = Decimal("100000"), Decimal("110000") if action == "CAPITAL_INCREASE" else Decimal("100000")
        approval = _family_approval(evidence, action, current=str(current), requested=str(requested))
        verdict = G.evaluate_family_action(
            evidence=evidence, action=action, current_capital_inr=current,
            requested_capital_inr=requested, owner_approval=approval, now=NOW,
        )
        assert verdict.approved is False
        assert reason in verdict.reasons


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
