from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from financial_brain.forward_edge.manual_paper_decisions import (
    ManualPaperAction,
    ManualPaperDecision,
    ManualPaperDecisionError,
    ManualPaperDecisionJournal,
    paper_order_sha256,
)
from financial_brain.forward_edge.ui_actions import UIAction, authorize_ui_action
from financial_brain.kernel.oms import State
from financial_brain.kernel.paper_account import PaperAccount


def _decision(action: ManualPaperAction, *, digest: str | None = None) -> ManualPaperDecision:
    return ManualPaperDecision(
        decision_id=f"decision-{action.value.lower()}", account_id="manual-a", desk_id="SWING",
        candidate_id="candidate-1", action=action, actor_id="trader-1",
        decided_at=datetime(2026, 11, 22, 10, tzinfo=timezone.utc),
        reason=f"human chose {action.value}", order_sha256=digest,
    )


def test_approve_binds_exact_order_and_creates_only_paper_receipt(tmp_path) -> None:
    now = datetime(2026, 11, 22, 10, tzinfo=timezone.utc)
    intent = {"client_id": "paper-1", "symbol": "NSE:INFY", "side": "BUY", "quantity": 2,
              "limit": "100", "reserve": "200"}
    digest = paper_order_sha256(**intent)
    decision = _decision(ManualPaperAction.APPROVE, digest=digest)
    journal = ManualPaperDecisionJournal(tmp_path / "decisions.sqlite")
    assert journal.append(decision) is True
    assert journal.append(decision) is False
    assert journal.decisions(account_id="manual-a", desk_id="SWING") == (decision.body(),)

    book = PaperAccount(tmp_path / "manual-book.jsonl", account_id="manual-a",
                        initial_cash=Decimal("1000"), execution_mode="TRADER_APPROVED")
    receipt = decision.to_trader_approval(submitted_at=now + timedelta(seconds=1))
    book.submit_intent(client_id="paper-1", symbol="NSE:INFY", side="BUY", quantity=2,
                       limit=Decimal("100"), reserve=Decimal("200"),
                       approval_receipt=receipt, submitted_at=now + timedelta(seconds=1))
    book.move("paper-1", State.SUBMITTED)
    book.move("paper-1", State.ACCEPTED)
    book.record_fill("paper-1", fill_id="paper-fill-1", quantity=2, price=Decimal("99"))
    assert book.position("NSE:INFY").quantity == 2
    assert book.cash == Decimal("802")
    assert book.execution_mode == "TRADER_APPROVED"
    assert not authorize_ui_action(UIAction.SUBMIT_ORDER, now=now).allowed
    journal.close()


@pytest.mark.parametrize("action", [ManualPaperAction.REJECT, ManualPaperAction.WATCH,
                                      ManualPaperAction.NO_TRADE])
def test_non_approve_choice_never_produces_order_authorization(action) -> None:
    decision = _decision(action)
    with pytest.raises(ManualPaperDecisionError, match="cannot authorize"):
        decision.to_trader_approval(submitted_at=decision.decided_at + timedelta(seconds=1))


def test_approve_requires_exact_hash_and_human_actor() -> None:
    with pytest.raises(ManualPaperDecisionError, match="exact paper order"):
        _decision(ManualPaperAction.APPROVE)
    with pytest.raises(ManualPaperDecisionError, match="human actor"):
        ManualPaperDecision(
            decision_id="bot-approval", account_id="manual-a", desk_id="SWING",
            candidate_id="candidate-1", action=ManualPaperAction.APPROVE, actor_id="bot:trader",
            decided_at=datetime.now(timezone.utc), reason="automated choice", order_sha256="a" * 64,
        )


def test_decision_journal_rejects_reused_id_with_new_action(tmp_path) -> None:
    journal = ManualPaperDecisionJournal(tmp_path / "decisions.sqlite")
    journal.append(_decision(ManualPaperAction.REJECT))
    changed = ManualPaperDecision(
        decision_id="decision-reject", account_id="manual-a", desk_id="SWING",
        candidate_id="candidate-1", action=ManualPaperAction.APPROVE, actor_id="trader-1",
        decided_at=datetime(2026, 11, 22, 10, tzinfo=timezone.utc), reason="changed action",
        order_sha256="a" * 64,
    )
    with pytest.raises(ManualPaperDecisionError, match="reused"):
        journal.append(changed)
    journal.close()
