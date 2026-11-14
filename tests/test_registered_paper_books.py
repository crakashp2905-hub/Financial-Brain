from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from financial_brain.kernel.paper_account import FeeSchedule
from financial_brain.kernel.registered_paper_books import (
    FrozenPaperBookPlan,
    PaperBookRegistryError,
    RegisteredPaperBooks,
    StrategyBookAllocation,
)


REGISTERED = datetime(2026, 11, 13, 10, tzinfo=timezone.utc)
EFFECTIVE = datetime(2026, 11, 16, 3, 30, tzinfo=timezone.utc)
FEE = FeeSchedule("paper-v1", REGISTERED.date(), Decimal("1"), Decimal("1"))


def plan(**changes):
    values = dict(
        plan_id="allocation-v1",
        registered_at=REGISTERED,
        effective_from=EFFECTIVE,
        total_capital_inr=Decimal("1000"),
        books=(StrategyBookAllocation("alpha", "paper-alpha", Decimal("600")),
               StrategyBookAllocation("beta", "paper-beta", Decimal("400"))),
        eligible_strategy_ids=("alpha", "beta"),
        eligibility_evidence_id="eligibility:cohort-1",
        registration_evidence_id="owner-registration:initial-1",
    )
    values.update(changes)
    return FrozenPaperBookPlan(**values)


def test_registered_plan_opens_distinct_persistent_accounts_and_cash(tmp_path):
    registry_path = tmp_path / "allocation.journal"
    registry = RegisteredPaperBooks(registry_path)
    original = plan()
    digest = registry.register(original)
    assert digest == original.sha256
    assert registry.register(original) == digest

    accounts = registry.open_accounts(plan_id=original.plan_id, root=tmp_path / "books", fee_schedule=FEE)
    assert set(accounts) == {"alpha", "beta"}
    assert accounts["alpha"].account_id == "paper-alpha"
    assert accounts["beta"].account_id == "paper-beta"
    assert accounts["alpha"].cash == Decimal("600")
    assert accounts["beta"].cash == Decimal("400")
    assert accounts["alpha"].path != accounts["beta"].path

    reopened = RegisteredPaperBooks(registry_path)
    restored = reopened.open_accounts(plan_id=original.plan_id, root=tmp_path / "books", fee_schedule=FEE)
    assert restored["alpha"].cash == Decimal("600")
    assert restored["beta"].cash == Decimal("400")


def test_strategy_order_uses_only_its_registered_book_and_reservation(tmp_path):
    registry = RegisteredPaperBooks(tmp_path / "allocation.journal")
    frozen = plan()
    registry.register(frozen)
    books = registry.open_accounts(plan_id=frozen.plan_id, root=tmp_path / "books", fee_schedule=FEE)

    with pytest.raises(PaperBookRegistryError, match="another strategy"):
        registry.submit_intent(plan_id=frozen.plan_id, strategy_id="alpha", account=books["beta"],
                              session_at=EFFECTIVE, client_id="cross-book", symbol="NSE:INFY",
                              side="BUY", quantity=5, limit=Decimal("10"), reserve=Decimal("51"))
    registry.submit_intent(plan_id=frozen.plan_id, strategy_id="alpha", account=books["alpha"],
                           session_at=EFFECTIVE, client_id="alpha-order", symbol="NSE:INFY",
                           side="BUY", quantity=5, limit=Decimal("10"), reserve=Decimal("51"))
    assert books["alpha"].available_cash == Decimal("549")
    assert books["beta"].available_cash == Decimal("400")


def test_unregistered_or_late_allocation_changes_are_rejected(tmp_path):
    registry = RegisteredPaperBooks(tmp_path / "allocation.journal")
    first = plan()
    first_hash = registry.register(first)
    with pytest.raises(PaperBookRegistryError, match="requires a registered change ID"):
        plan(plan_id="allocation-v2", registered_at=EFFECTIVE,
             effective_from=datetime(2026, 11, 17, 3, 30, tzinfo=timezone.utc),
             previous_plan_sha256=first_hash)
    amended = plan(plan_id="allocation-v2", registered_at=EFFECTIVE - timedelta(hours=1),
                   effective_from=datetime(2026, 11, 17, 3, 30, tzinfo=timezone.utc),
                   previous_plan_sha256=first_hash, allocation_change_id="change-2026-11-17",
                   registration_evidence_id="owner-registration:change-2")
    registry.register(amended)
    resolved, allocation = registry.resolve(strategy_id="alpha", at=amended.effective_from)
    assert resolved.plan_id == amended.plan_id
    assert allocation.account_id == "paper-alpha"


def test_plan_rejects_duplicate_accounts_and_unbudgeted_capital():
    with pytest.raises(PaperBookRegistryError, match="account IDs must be unique"):
        plan(books=(StrategyBookAllocation("alpha", "shared", Decimal("600")),
                    StrategyBookAllocation("beta", "shared", Decimal("400"))))
    with pytest.raises(PaperBookRegistryError, match="exceeds approved total"):
        plan(total_capital_inr=Decimal("900"))


def test_plan_books_must_cover_the_frozen_eligible_strategy_manifest():
    with pytest.raises(PaperBookRegistryError, match="exactly cover"):
        plan(eligible_strategy_ids=("alpha",))
    with pytest.raises(PaperBookRegistryError, match="eligibility evidence"):
        plan(eligibility_evidence_id=" ")


def test_plan_changes_cannot_reassign_or_resize_an_existing_account(tmp_path):
    registry = RegisteredPaperBooks(tmp_path / "allocation.journal")
    first = plan()
    first_hash = registry.register(first)
    changed_books = (StrategyBookAllocation("alpha", "paper-alpha", Decimal("500")),
                     StrategyBookAllocation("beta", "paper-beta", Decimal("400")))
    changed = plan(plan_id="allocation-v2", registered_at=EFFECTIVE,
                   effective_from=datetime(2026, 11, 17, 3, 30, tzinfo=timezone.utc),
                   previous_plan_sha256=first_hash, allocation_change_id="change-17",
                   books=changed_books)
    with pytest.raises(PaperBookRegistryError, match="cannot be reassigned or resized"):
        registry.register(changed)


def test_transfer_api_never_moves_cash_between_books(tmp_path):
    registry = RegisteredPaperBooks(tmp_path / "allocation.journal")
    with pytest.raises(PaperBookRegistryError, match="transfer is forbidden"):
        registry.transfer("paper-alpha", "paper-beta", Decimal("1"))

