import dataclasses
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from financial_brain.kernel.paper_account import FeeSchedule
from financial_brain.forward_edge.durable import DurableJournal
from financial_brain.forward_edge.approvals import Approval
from financial_brain.forward_edge.governance import FamilyEvidence, family_action_scope_sha256
from financial_brain.kernel.registered_paper_books import (
    FamilyCapitalAuthorization,
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
    if "family_capital_authorizations" not in changes:
        values["family_capital_authorizations"] = tuple(
            family_authorization(book.strategy_id, "INITIAL_ALLOCATION", Decimal(0), book.capital_inr)
            for book in values["books"]
        )
    return FrozenPaperBookPlan(**values)


def family_authorization(strategy_id, action, current, requested, *, status="VERIFIED", actor_type="human"):
    evidence = FamilyEvidence(
        family_id=strategy_id, status=status, strategy_sha256="a" * 64,
        frozen_protocol_sha256="b" * 64, evidence_manifest_sha256="c" * 64,
        protocol_frozen=True, protocol_frozen_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        cohort_started_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        verified_sessions=80, receipt_sessions=80, candidate_count=4,
        multiplicity_adjusted=True, cost_stress_passed=True,
        matched_control_lcb_bps=Decimal("2"), net_alpha_lcb_bps=Decimal("1"),
    )
    scope = family_action_scope_sha256(
        evidence=evidence, action=action, current_capital_inr=current,
        requested_capital_inr=requested, execution_mode="PAPER",
    )
    approval_kind = "CAPITAL" if action == "CAPITAL_INCREASE" else "STRATEGY_ENROLLMENT"
    approval = Approval(
        approval_kind, strategy_id, scope, "owner:test", actor_type,
        datetime(2026, 11, 12, tzinfo=timezone.utc),
        datetime(2026, 12, 1, tzinfo=timezone.utc),
    )
    return FamilyCapitalAuthorization(strategy_id, action, current, requested, evidence, approval)


def automation_approval(evidence, strategy_id, capital):
    scope = family_action_scope_sha256(
        evidence=evidence, action="AUTO_EXECUTION_ENABLE",
        current_capital_inr=capital, requested_capital_inr=capital,
        execution_mode="PAPER",
    )
    return Approval(
        "STRATEGY_ENROLLMENT", strategy_id, scope, "owner:test", "human",
        datetime(2026, 11, 12, tzinfo=timezone.utc),
        datetime(2026, 12, 1, tzinfo=timezone.utc),
    )


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
    alpha_authorization = frozen.family_capital_authorizations[0]
    with pytest.raises(PaperBookRegistryError, match="SCOPED_HUMAN_OWNER_APPROVAL_REQUIRED"):
        registry.submit_intent(
            plan_id=frozen.plan_id, strategy_id="alpha", account=books["alpha"],
            session_at=EFFECTIVE, client_id="blocked-alpha-order", symbol="NSE:INFY",
            side="BUY", quantity=5, limit=Decimal("10"), reserve=Decimal("51"),
            family_evidence=alpha_authorization.evidence,
        )
    registry.submit_intent(plan_id=frozen.plan_id, strategy_id="alpha", account=books["alpha"],
                           session_at=EFFECTIVE, client_id="alpha-order", symbol="NSE:INFY",
                           side="BUY", quantity=5, limit=Decimal("10"), reserve=Decimal("51"),
                           family_evidence=alpha_authorization.evidence,
                           owner_approval=automation_approval(
                               alpha_authorization.evidence, "alpha", Decimal("600")))
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


def test_every_initial_family_allocation_needs_evidence_and_owner_approval(tmp_path):
    missing = dataclasses.replace(plan(), family_capital_authorizations=())
    registry = RegisteredPaperBooks(tmp_path / "missing.journal")
    with pytest.raises(PaperBookRegistryError, match="exactly one family capital authorization"):
        registry.register(missing)


def test_family_capital_increase_requires_exact_scoped_approval_and_replays(tmp_path):
    path = tmp_path / "increase.journal"
    registry = RegisteredPaperBooks(path)
    first = plan()
    first_hash = registry.register(first)
    increased = plan(
        plan_id="allocation-v2", registered_at=EFFECTIVE - timedelta(hours=1),
        effective_from=datetime(2026, 11, 17, 3, 30, tzinfo=timezone.utc),
        total_capital_inr=Decimal("1100"),
        books=(StrategyBookAllocation("alpha", "paper-alpha-v2", Decimal("100")),
               StrategyBookAllocation("beta", "paper-beta", Decimal("400"))),
        previous_plan_sha256=first_hash, allocation_change_id="increase-alpha-1",
        family_capital_authorizations=(family_authorization(
            "alpha", "CAPITAL_INCREASE", Decimal("600"), Decimal("700")),
            first.family_capital_authorizations[1]),
    )
    digest = registry.register(increased)
    assert digest == increased.sha256
    reopened = RegisteredPaperBooks(path)
    assert reopened.plans[-1].family_capital_authorizations[0].requested_capital_inr == Decimal("700")


def test_new_family_allocation_with_failed_evidence_is_rejected(tmp_path):
    registry = RegisteredPaperBooks(tmp_path / "failed-evidence.journal")
    first = plan()
    first_hash = registry.register(first)
    allocation = plan(
        plan_id="allocation-v2", registered_at=EFFECTIVE - timedelta(hours=1),
        effective_from=datetime(2026, 11, 17, 3, 30, tzinfo=timezone.utc),
        total_capital_inr=Decimal("1100"),
        books=(StrategyBookAllocation("alpha", "paper-alpha-v2", Decimal("100")),
               StrategyBookAllocation("beta", "paper-beta", Decimal("400"))),
        previous_plan_sha256=first_hash, allocation_change_id="increase-alpha-2",
        family_capital_authorizations=(family_authorization(
            "alpha", "CAPITAL_INCREASE", Decimal("600"), Decimal("700"),
            status="SHADOW_ONLY"), first.family_capital_authorizations[1]),
    )
    with pytest.raises(PaperBookRegistryError, match="FAMILY_STATUS_NOT_VERIFIED"):
        registry.register(allocation)


def test_legacy_plan_loads_but_orders_still_need_current_family_approval(tmp_path):
    path = tmp_path / "legacy.journal"
    legacy = dataclasses.replace(plan(), family_capital_authorizations=())
    DurableJournal(path).append({"t": "FROZEN_PAPER_BOOK_PLAN", "plan_sha256": legacy.sha256,
                                 "plan": legacy.as_dict()})
    restored = RegisteredPaperBooks(path)
    assert restored.resolve(strategy_id="alpha", at=EFFECTIVE)[0].plan_id == legacy.plan_id
    accounts = restored.open_accounts(plan_id=legacy.plan_id, root=tmp_path / "legacy-books", fee_schedule=FEE)
    evidence = family_authorization("alpha", "INITIAL_ALLOCATION", Decimal(0), Decimal("600")).evidence
    with pytest.raises(PaperBookRegistryError, match="SCOPED_HUMAN_OWNER_APPROVAL_REQUIRED"):
        restored.submit_intent(
            plan_id=legacy.plan_id, strategy_id="alpha", account=accounts["alpha"],
            session_at=EFFECTIVE, client_id="legacy-no-approval", symbol="NSE:INFY",
            side="BUY", quantity=1, limit=Decimal("10"), reserve=Decimal("11"),
            family_evidence=evidence,
        )

