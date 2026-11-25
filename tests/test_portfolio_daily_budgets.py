from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from financial_brain.portfolio.daily_budgets import (
    BudgetDisposition,
    DailyBudgetError,
    DailyBudgetPolicy,
    ProposedOrderBudget,
    ReservedOrderBudget,
    evaluate_daily_budgets,
)


SESSION = date(2026, 11, 26)
IST = timezone(timedelta(hours=5, minutes=30))
FREEZE = datetime(2026, 11, 25, 15, 30, tzinfo=IST)
OPEN = datetime(2026, 11, 26, 9, 15, tzinfo=IST)
AS_OF = datetime(2026, 11, 26, 15, 30, tzinfo=IST)


def _policy(**changes):
    values = {
        "scope_id": "paper-account-1",
        "session_date": SESSION,
        "policy_version": "v1",
        "frozen_at": FREEZE,
        "maximum_turnover_inr": Decimal("1000"),
        "maximum_commission_inr": Decimal("100"),
        "source_sha256": "a" * 64,
    }
    values.update(changes)
    return DailyBudgetPolicy(**values)


def _proposal(order_id, turnover, commission, priority):
    return ProposedOrderBudget(order_id, "paper-account-1", SESSION,
                               OPEN + timedelta(minutes=priority + 1),
                               Decimal(turnover), Decimal(commission), priority, "b" * 64)


def test_turnover_and_commission_caps_are_separate_and_priority_ordered():
    result = evaluate_daily_budgets(
        policy=_policy(), as_of=AS_OF, existing_reservations=(),
        proposed_orders=(
            _proposal("high-priority", "600", "40", 10),
            _proposal("turnover-over", "500", "20", 5),
            _proposal("commission-over", "100", "70", 1),
        ),
    )
    assert [(row.order_id, row.disposition) for row in result.decisions] == [
        ("high-priority", BudgetDisposition.ACCEPTED),
        ("turnover-over", BudgetDisposition.REJECTED_TURNOVER_CAP),
        ("commission-over", BudgetDisposition.REJECTED_COMMISSION_CAP),
    ]
    assert result.turnover_used_inr == Decimal("600")
    assert result.commission_used_inr == Decimal("40")
    assert result.turnover_remaining_inr == Decimal("400")
    assert result.commission_remaining_inr == Decimal("60")
    assert len(result.input_evidence_sha256) == len(result.audit_sha256) == 64
    assert result == evaluate_daily_budgets(
        policy=_policy(), as_of=AS_OF, existing_reservations=(),
        proposed_orders=(
            _proposal("high-priority", "600", "40", 10),
            _proposal("turnover-over", "500", "20", 5),
            _proposal("commission-over", "100", "70", 1),
        ),
    )


def test_existing_reservations_count_before_proposals_and_breach_halts_new_orders():
    reserved = ReservedOrderBudget("old", "paper-account-1", SESSION, OPEN,
                                   Decimal("400"), Decimal("50"), "c" * 64)
    result = evaluate_daily_budgets(
        policy=_policy(), as_of=AS_OF, existing_reservations=(reserved,),
        proposed_orders=(_proposal("new", "500", "50", 1),),
    )
    assert result.decisions[0].disposition is BudgetDisposition.ACCEPTED
    assert result.turnover_used_inr == Decimal("900")
    assert result.commission_used_inr == Decimal("100")

    breached = ReservedOrderBudget("old-2", "paper-account-1", SESSION, OPEN,
                                   Decimal("1100"), Decimal("120"), "d" * 64)
    halted = evaluate_daily_budgets(
        policy=_policy(), as_of=AS_OF, existing_reservations=(breached,),
        proposed_orders=(_proposal("new", "1", "1", 1),),
    )
    assert halted.decisions[0].disposition is BudgetDisposition.HALTED_EXISTING_BUDGET_BREACH
    assert halted.turnover_used_inr == Decimal("1100")
    assert halted.turnover_remaining_inr == 0


def test_duplicate_ids_scope_session_and_bad_time_are_rejected():
    proposal = _proposal("same", "1", "1", 1)
    with pytest.raises(DailyBudgetError, match="unique"):
        evaluate_daily_budgets(policy=_policy(), as_of=AS_OF, existing_reservations=(),
                               proposed_orders=(proposal, proposal))
    with pytest.raises(DailyBudgetError, match="match the budget scope"):
        evaluate_daily_budgets(policy=_policy(), as_of=AS_OF, existing_reservations=(),
                               proposed_orders=(ProposedOrderBudget(
                                   "other", "different", SESSION, OPEN, 1, 1, 1, "b" * 64),))
    with pytest.raises(DailyBudgetError, match="frozen session through as-of"):
        evaluate_daily_budgets(policy=_policy(), as_of=AS_OF, existing_reservations=(),
                               proposed_orders=(ProposedOrderBudget(
                                   "too-early", "paper-account-1", SESSION, FREEZE, 1, 1, 1, "b" * 64),))


def test_invalid_policy_or_negative_commission_is_rejected():
    with pytest.raises(DailyBudgetError, match="SHA-256"):
        _policy(source_sha256="missing")
    with pytest.raises(DailyBudgetError, match="non-negative"):
        _proposal("bad", "1", "-1", 1)
    with pytest.raises(DailyBudgetError, match="session date"):
        evaluate_daily_budgets(policy=_policy(),
                               as_of=datetime(2026, 11, 27, 10, tzinfo=IST),
                               existing_reservations=(), proposed_orders=())
