"""Frozen strategy-to-account capital allocations for isolated paper books."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re

from financial_brain.forward_edge.durable import DurableJournal
from financial_brain.kernel.paper_account import FeeSchedule, PaperAccount


class PaperBookRegistryError(ValueError):
    """Allocation registration or account-isolation failure."""


_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,96}$")


def _aware(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PaperBookRegistryError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class StrategyBookAllocation:
    strategy_id: str
    account_id: str
    capital_inr: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.strategy_id, str) or not _ID.fullmatch(self.strategy_id):
            raise PaperBookRegistryError("strategy_id must be a stable identifier")
        if not isinstance(self.account_id, str) or not _ID.fullmatch(self.account_id):
            raise PaperBookRegistryError("account_id must be a stable identifier")
        try:
            capital = Decimal(self.capital_inr)
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise PaperBookRegistryError("capital must be a finite positive Decimal") from exc
        if not capital.is_finite() or capital <= 0:
            raise PaperBookRegistryError("capital must be a finite positive Decimal")
        object.__setattr__(self, "capital_inr", capital)

    def as_dict(self) -> dict:
        return {"strategy_id": self.strategy_id, "account_id": self.account_id,
                "capital_inr": format(self.capital_inr, "f")}


@dataclass(frozen=True)
class FrozenPaperBookPlan:
    plan_id: str
    registered_at: datetime
    effective_from: datetime
    total_capital_inr: Decimal
    books: tuple[StrategyBookAllocation, ...]
    eligible_strategy_ids: tuple[str, ...]
    eligibility_evidence_id: str
    registration_evidence_id: str
    previous_plan_sha256: str | None = None
    allocation_change_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.plan_id, str) or not _ID.fullmatch(self.plan_id):
            raise PaperBookRegistryError("plan_id must be a stable identifier")
        registered = _aware(self.registered_at, "registered_at")
        effective = _aware(self.effective_from, "effective_from")
        if registered >= effective:
            raise PaperBookRegistryError("allocation must be registered before it becomes effective")
        object.__setattr__(self, "registered_at", registered)
        object.__setattr__(self, "effective_from", effective)
        try:
            total = Decimal(self.total_capital_inr)
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise PaperBookRegistryError("total capital must be a finite positive Decimal") from exc
        if not total.is_finite() or total <= 0:
            raise PaperBookRegistryError("total capital must be a finite positive Decimal")
        object.__setattr__(self, "total_capital_inr", total)
        books = tuple(self.books)
        if not books or any(not isinstance(book, StrategyBookAllocation) for book in books):
            raise PaperBookRegistryError("at least one typed strategy allocation is required")
        if len({book.strategy_id for book in books}) != len(books):
            raise PaperBookRegistryError("strategy IDs must be unique within a plan")
        if len({book.account_id for book in books}) != len(books):
            raise PaperBookRegistryError("account IDs must be unique within a plan")
        if sum((book.capital_inr for book in books), Decimal(0)) > total:
            raise PaperBookRegistryError("allocated strategy capital exceeds approved total capital")
        object.__setattr__(self, "books", tuple(sorted(books, key=lambda book: book.strategy_id)))
        raw_eligible = tuple(self.eligible_strategy_ids)
        if any(not isinstance(strategy_id, str) or not _ID.fullmatch(strategy_id) for strategy_id in raw_eligible):
            raise PaperBookRegistryError("eligible strategy IDs must be stable identifiers")
        eligible = tuple(sorted(set(raw_eligible)))
        if set(eligible) != {book.strategy_id for book in books}:
            raise PaperBookRegistryError("paper books must exactly cover the frozen eligible strategy set")
        object.__setattr__(self, "eligible_strategy_ids", eligible)
        if not isinstance(self.eligibility_evidence_id, str) or not self.eligibility_evidence_id.strip():
            raise PaperBookRegistryError("frozen eligibility evidence is required")
        if not isinstance(self.registration_evidence_id, str) or not self.registration_evidence_id.strip():
            raise PaperBookRegistryError("allocation registration evidence is required")
        if self.previous_plan_sha256 is not None:
            if not re.fullmatch(r"[0-9a-f]{64}", self.previous_plan_sha256):
                raise PaperBookRegistryError("previous allocation hash is invalid")
            if not isinstance(self.allocation_change_id, str) or not self.allocation_change_id.strip():
                raise PaperBookRegistryError("allocation change requires a registered change ID")
        elif self.allocation_change_id is not None:
            raise PaperBookRegistryError("initial plan cannot reference a change to a prior plan")

    def as_dict(self) -> dict:
        return {
            "plan_id": self.plan_id,
            "registered_at": self.registered_at.isoformat(),
            "effective_from": self.effective_from.isoformat(),
            "total_capital_inr": format(self.total_capital_inr, "f"),
            "books": [book.as_dict() for book in self.books],
            "eligible_strategy_ids": list(self.eligible_strategy_ids),
            "eligibility_evidence_id": self.eligibility_evidence_id,
            "registration_evidence_id": self.registration_evidence_id,
            "previous_plan_sha256": self.previous_plan_sha256,
            "allocation_change_id": self.allocation_change_id,
        }

    @property
    def sha256(self) -> str:
        body = json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        return hashlib.sha256(body).hexdigest()


def _plan_from_dict(value: dict) -> FrozenPaperBookPlan:
    return FrozenPaperBookPlan(
        plan_id=value["plan_id"], registered_at=datetime.fromisoformat(value["registered_at"]),
        effective_from=datetime.fromisoformat(value["effective_from"]),
        total_capital_inr=Decimal(value["total_capital_inr"]),
        books=tuple(StrategyBookAllocation(row["strategy_id"], row["account_id"],
                                           Decimal(row["capital_inr"])) for row in value["books"]),
        eligible_strategy_ids=tuple(value["eligible_strategy_ids"]),
        eligibility_evidence_id=value["eligibility_evidence_id"],
        registration_evidence_id=value["registration_evidence_id"],
        previous_plan_sha256=value["previous_plan_sha256"],
        allocation_change_id=value["allocation_change_id"],
    )


class RegisteredPaperBooks:
    """Hash-journaled allocation registry; accounts are separate persistent PaperAccount journals."""

    def __init__(self, journal_path: Path) -> None:
        self.journal = DurableJournal(Path(journal_path))
        self._plans: list[tuple[FrozenPaperBookPlan, str]] = []
        for row in self.journal.records():
            if row.get("t") != "FROZEN_PAPER_BOOK_PLAN":
                raise PaperBookRegistryError("unexpected record in paper-book allocation journal")
            plan = _plan_from_dict(row["plan"])
            if plan.sha256 != row.get("plan_sha256"):
                raise PaperBookRegistryError("paper-book plan hash does not match its contents")
            self._validate_successor(plan)
            self._plans.append((plan, plan.sha256))

    @property
    def plans(self) -> tuple[FrozenPaperBookPlan, ...]:
        return tuple(plan for plan, _ in self._plans)

    def _validate_successor(self, plan: FrozenPaperBookPlan) -> None:
        if any(existing.plan_id == plan.plan_id for existing, _ in self._plans):
            raise PaperBookRegistryError("allocation plan ID already exists")
        if not self._plans:
            if plan.previous_plan_sha256 is not None:
                raise PaperBookRegistryError("first plan cannot name a predecessor")
            return
        previous, previous_hash = self._plans[-1]
        if plan.previous_plan_sha256 != previous_hash:
            raise PaperBookRegistryError("allocation change must reference the latest registered plan")
        if plan.effective_from <= previous.effective_from:
            raise PaperBookRegistryError("new allocation must take effect after the prior plan")
        old_books = {book.account_id: book for prior_plan, _digest in self._plans
                     for book in prior_plan.books}
        new_books = {book.account_id: book for book in plan.books}
        for account_id in old_books.keys() & new_books.keys():
            old, new = old_books[account_id], new_books[account_id]
            if old.strategy_id != new.strategy_id or old.capital_inr != new.capital_inr:
                raise PaperBookRegistryError("an account cannot be reassigned or resized; register a fresh account ID")
        issued_capital = sum((book.capital_inr for book in old_books.values()), Decimal(0))
        new_account_capital = sum((book.capital_inr for book in plan.books if book.account_id not in old_books),
                                  Decimal(0))
        if issued_capital + new_account_capital > plan.total_capital_inr:
            raise PaperBookRegistryError("new paper-account capital would exceed the cumulative approved cash budget")

    def register(self, plan: FrozenPaperBookPlan) -> str:
        prior = next(((existing, digest) for existing, digest in self._plans if existing.plan_id == plan.plan_id), None)
        if prior is not None:
            if prior[0] != plan:
                raise PaperBookRegistryError("registered plan ID is immutable")
            return prior[1]
        self._validate_successor(plan)
        digest = plan.sha256
        self.journal.append({"t": "FROZEN_PAPER_BOOK_PLAN", "plan_sha256": digest, "plan": plan.as_dict()})
        self._plans.append((plan, digest))
        return digest

    def resolve(self, *, strategy_id: str, at: datetime) -> tuple[FrozenPaperBookPlan, StrategyBookAllocation]:
        when = _aware(at, "at")
        eligible = [(plan, digest) for plan, digest in self._plans if plan.effective_from <= when]
        if not eligible:
            raise PaperBookRegistryError("no allocation plan is effective for this paper session")
        plan, _ = eligible[-1]
        allocation = next((book for book in plan.books if book.strategy_id == strategy_id), None)
        if allocation is None:
            raise PaperBookRegistryError("strategy is not eligible in the effective frozen book plan")
        return plan, allocation

    def open_accounts(self, *, plan_id: str, root: Path, fee_schedule: FeeSchedule) -> dict[str, PaperAccount]:
        plan = next((plan for plan, _ in self._plans if plan.plan_id == plan_id), None)
        if plan is None:
            raise PaperBookRegistryError("paper accounts require a registered plan")
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        accounts = {}
        for allocation in plan.books:
            path_id = hashlib.sha256(allocation.account_id.encode()).hexdigest()[:24]
            accounts[allocation.strategy_id] = PaperAccount(
                root / f"{path_id}.jsonl", account_id=allocation.account_id,
                initial_cash=allocation.capital_inr, currency="INR", fee_schedule=fee_schedule,
            )
        return accounts

    def submit_intent(self, *, plan_id: str, strategy_id: str, account: PaperAccount,
                      session_at: datetime, client_id: str, symbol: str, side: str,
                      quantity: int, limit: Decimal | None, reserve: Decimal) -> None:
        plan, allocation = self.resolve(strategy_id=strategy_id, at=session_at)
        if plan.plan_id != plan_id:
            raise PaperBookRegistryError("session is not using the requested effective allocation plan")
        if account.account_id != allocation.account_id:
            raise PaperBookRegistryError("strategy cannot submit through another strategy's paper account")
        account.submit_intent(client_id=client_id, symbol=symbol, side=side,
                              quantity=quantity, limit=limit, reserve=reserve)

    def transfer(self, *_args, **_kwargs) -> None:
        raise PaperBookRegistryError("cash transfer is forbidden; register a new allocation plan effective in a future session")
