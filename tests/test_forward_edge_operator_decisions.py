from datetime import datetime, timezone

import pytest

from financial_brain.forward_edge.operator_decisions import (
    OperatorAction,
    OperatorDecision,
    OperatorDecisionError,
    OperatorDecisionJournal,
    live_lock_reasons,
    render_operator_panel,
)


NOW = datetime(2026, 11, 16, 4, tzinfo=timezone.utc)


def _decision(action=OperatorAction.PAUSE, **overrides):
    values = {"decision_id": "decision-1", "session_id": "session-1",
              "actor_id": "Ramesh", "actor_type": "human", "action": action,
              "reason": "inspect feed quality", "decided_at": NOW,
              "evidence_sha256": "a" * 64}
    values.update(overrides)
    return OperatorDecision(**values)


def test_operator_choice_journal_is_idempotent_restart_safe_and_immutable(tmp_path):
    path = tmp_path / "operator.sqlite3"
    journal = OperatorDecisionJournal(path)
    decision = _decision(OperatorAction.INVESTIGATE)
    assert journal.append(decision)
    assert not journal.append(decision)
    expected = journal.session_decisions("session-1")
    journal.close()
    reopened = OperatorDecisionJournal(path)
    assert reopened.session_decisions("session-1") == expected
    with pytest.raises(OperatorDecisionError, match="different content"):
        reopened.append(_decision(OperatorAction.REJECT))
    reopened.close()


@pytest.mark.parametrize("action", tuple(OperatorAction))
def test_all_five_operator_decisions_are_supported_without_granting_execution(action):
    assert _decision(action).action is action
    assert action.value in {"CONTINUE_PAPER", "PAUSE", "INVESTIGATE", "REJECT", "REQUEST_REVIEW"}


def test_live_lock_reasons_and_operator_page_never_claim_live_enabled(tmp_path):
    audit = {"overall": "RED", "blocking": ["independent timestamp: missing"],
             "checks": [{"name": "custody", "status": "RED", "reason": "root missing"},
                        {"name": "mode", "status": "GREEN", "reason": "paper only"}]}
    reasons = live_lock_reasons(audit)
    assert any("independent timestamp" in reason for reason in reasons)
    assert any("physical_orders" in reason for reason in reasons)
    page = render_operator_panel(audit_report=audit, decisions=(),
                                 output=tmp_path / "operator.html").read_text(encoding="utf-8")
    assert "LIVE_CASH LOCKED" in page
    assert "do not enable live mode" in page
    assert "No human operator decision recorded" in page


def test_nonhuman_actors_and_explicit_evidence_identity_are_required():
    with pytest.raises(OperatorDecisionError, match="named human"):
        _decision(actor_id="agent:fb", actor_type="human")
    with pytest.raises(OperatorDecisionError, match="SHA-256"):
        _decision(evidence_sha256="bad")
