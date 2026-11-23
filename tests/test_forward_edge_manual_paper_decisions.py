from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.forward_edge.manual_paper_decisions import (
    ManualPaperAction,
    ManualPaperDecision,
    ManualPaperDecisionError,
    ManualPaperDecisionJournal,
    ManualPaperState,
)

NOW = datetime(2026, 11, 24, 10, tzinfo=timezone.utc)
HASH = "a" * 64


def _decision(action: ManualPaperAction = ManualPaperAction.APPROVE, **overrides) -> ManualPaperDecision:
    values = {
        "decision_id": "decision-1", "session_id": "paper-session-1", "candidate_id": "candidate-1",
        "candidate_sha256": HASH, "actor_id": "Ramesh", "actor_type": "human", "action": action,
        "reason": "reviewed candidate and evidence", "decided_at": NOW,
        "expires_at": NOW + timedelta(minutes=5), "candidate_expires_at": NOW + timedelta(minutes=10),
    }
    values.update(overrides)
    return ManualPaperDecision(**values)


@pytest.mark.parametrize("action,state", [
    (ManualPaperAction.APPROVE, ManualPaperState.APPROVED_PAPER_ONLY),
    (ManualPaperAction.REJECT, ManualPaperState.REJECTED),
    (ManualPaperAction.WATCH, ManualPaperState.WATCH),
    (ManualPaperAction.NO_TRADE, ManualPaperState.NO_TRADE),
])
def test_human_actions_are_scoped_to_frozen_candidate_and_never_authorize_physical_orders(action, state):
    decision = _decision(action)

    assert decision.resolve(now=NOW + timedelta(seconds=1), current_candidate_sha256=HASH,
                            candidate_paper_eligible=True) is state
    assert decision.body()["execution_scope"] == "PAPER_ONLY"
    assert decision.body()["physical_orders_authorized"] is False


def test_changed_candidate_stales_approval_and_ttl_or_candidate_expiry_blocks_it():
    decision = _decision()
    assert decision.resolve(now=NOW + timedelta(seconds=1), current_candidate_sha256="b" * 64,
                            candidate_paper_eligible=True) is ManualPaperState.STALE
    assert decision.resolve(now=NOW + timedelta(minutes=5), current_candidate_sha256=HASH,
                            candidate_paper_eligible=True) is ManualPaperState.EXPIRED


def test_approval_requires_paper_eligibility_and_cannot_outlive_candidate():
    with pytest.raises(ManualPaperDecisionError, match="not eligible"):
        _decision().resolve(now=NOW + timedelta(seconds=1), current_candidate_sha256=HASH,
                            candidate_paper_eligible=False)
    with pytest.raises(ManualPaperDecisionError, match="cannot outlive"):
        _decision(expires_at=NOW + timedelta(minutes=11))


def test_decision_journal_is_durable_idempotent_and_immutable(tmp_path):
    path = tmp_path / "manual-paper.sqlite3"
    decision = _decision()
    journal = ManualPaperDecisionJournal(path)
    assert journal.append(decision)
    assert not journal.append(decision)
    expected = journal.session_decisions("paper-session-1")
    journal.close()

    reopened = ManualPaperDecisionJournal(path)
    assert reopened.session_decisions("paper-session-1") == expected
    with pytest.raises(ManualPaperDecisionError, match="different content"):
        reopened.append(_decision(ManualPaperAction.REJECT))
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        reopened._con.execute("DELETE FROM manual_paper_decisions")
    reopened.close()


def test_manual_decision_rejects_agents_and_naive_times():
    with pytest.raises(ManualPaperDecisionError, match="named human"):
        _decision(actor_id="agent:stock", actor_type="human")
    with pytest.raises(ManualPaperDecisionError, match="timezone aware"):
        _decision(decided_at=datetime(2026, 11, 24, 10))
