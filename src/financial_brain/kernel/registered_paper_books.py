"""Frozen strategy-to-account capital allocations for isolated paper books."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re

from financial_brain.forward_edge.approvals import Approval
from financial_brain.forward_edge.durable import DurableJournal
from financial_brain.forward_edge.governance import (
    FamilyEvidence,
    GovernanceError,
    evaluate_family_action,
)
from financial_brain.kernel.paper_account import FeeSchedule, PaperAccount


class PaperBookRegistryError(ValueError):
    """Allocation registration or account-isolation failure."""


_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,96}$")


def _aware(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PaperBookRegistryError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _evidence_to_dict(evidence: FamilyEvidence) -> dict:
    value = dict(evidence.__dict__)
    value["protocol_frozen_at"] = evidence.protocol_frozen_at.isoformat()
    value["cohort_started_at"] = evidence.cohort_started_at.isoformat()
    value["matched_control_lcb_bps"] = str(evidence.matched_control_lcb_bps)
    value["net_alpha_lcb_bps"] = str(evidence.net_alpha_lcb_bps)
    return value


def _approval_to_dict(approval: Approval) -> dict:
    return {
        "kind": approval.kind, "subject": approval.subject,
        "scope_sha256": approval.scope_sha256, "actor_id": approval.actor_id,
        "actor_type": approval.actor_type, "approved_at_utc": approval.approved_at_utc.isoformat(),
        "expires_at_utc": approval.expires_at_utc.isoformat(), "adr_ref": approval.adr_ref,
    }


@dataclass(frozen=True)
class FamilyCapitalAuthorization:
    """Frozen per-family evidence and human approval for a paper-book allocation."""

    strategy_id: str
    action: str
    current_capital_inr: Decimal
    requested_capital_inr: Decimal
    evidence: FamilyEvidence
    owner_approval: Approval

    def __post_init__(self) -> None:
        if not isinstance(self.strategy_id, str) or not _ID.fullmatch(self.strategy_id):
            raise PaperBookRegistryError("capital authorization strategy_id must be stable")
        if self.action not in {"INITIAL_ALLOCATION", "CAPITAL_INCREASE"}:
            raise PaperBookRegistryError("capital authorization action is invalid")
        if not isinstance(self.evidence, FamilyEvidence) or self.evidence.family_id != self.strategy_id:
            raise PaperBookRegistryError("allocation authorization must name its strategy family")
        if not isinstance(self.owner_approval, Approval):
            raise PaperBookRegistryError("allocation authorization needs a typed human owner approval")
        try:
            current, requested = Decimal(self.current_capital_inr), Decimal(self.requested_capital_inr)
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise PaperBookRegistryError("authorization capital values must be finite decimals") from exc
        if not current.is_finite() or current < 0 or not requested.is_finite() or requested <= 0:
            raise PaperBookRegistryError("authorization capital must be non-negative then positive")
        object.__setattr__(self, "current_capital_inr", current)
        object.__setattr__(self, "requested_capital_inr", requested)

    def as_dict(self) -> dict:
        return {
            "strategy_id": self.strategy_id, "action": self.action,
            "current_capital_inr": format(self.current_capital_inr, "f"),
            "requested_capital_inr": format(self.requested_capital_inr, "f"),
            "evidence": _evidence_to_dict(self.evidence),
            "owner_approval": _approval_to_dict(self.owner_approval),
        }


def _authorization_from_dict(value: dict) -> FamilyCapitalAuthorization:
    raw_evidence = value["evidence"]
    evidence = FamilyEvidence(
        family_id=raw_evidence["family_id"], status=raw_evidence["status"],
        strategy_sha256=raw_evidence["strategy_sha256"],
        frozen_protocol_sha256=raw_evidence["frozen_protocol_sha256"],
        evidence_manifest_sha256=raw_evidence["evidence_manifest_sha256"],
        protocol_frozen=raw_evidence["protocol_frozen"],
        protocol_frozen_at=datetime.fromisoformat(raw_evidence["protocol_frozen_at"]),
        cohort_started_at=datetime.fromisoformat(raw_evidence["cohort_started_at"]),
        verified_sessions=raw_evidence["verified_sessions"],
        receipt_sessions=raw_evidence["receipt_sessions"],
        candidate_count=raw_evidence["candidate_count"],
        multiplicity_adjusted=raw_evidence["multiplicity_adjusted"],
        cost_stress_passed=raw_evidence["cost_stress_passed"],
        matched_control_lcb_bps=Decimal(raw_evidence["matched_control_lcb_bps"]),
        net_alpha_lcb_bps=Decimal(raw_evidence["net_alpha_lcb_bps"]),
    )
    raw_approval = value["owner_approval"]
    approval = Approval(
        kind=raw_approval["kind"], subject=raw_approval["subject"],
        scope_sha256=raw_approval["scope_sha256"], actor_id=raw_approval["actor_id"],
        actor_type=raw_approval["actor_type"],
        approved_at_utc=datetime.fromisoformat(raw_approval["approved_at_utc"]),
        expires_at_utc=datetime.fromisoformat(raw_approval["expires_at_utc"]),
        adr_ref=raw_approval.get("adr_ref"),
    )
    return FamilyCapitalAuthorization(
        strategy_id=value["strategy_id"], action=value["action"],
        current_capital_inr=Decimal(value["current_capital_inr"]),
        requested_capital_inr=Decimal(value["requested_capital_inr"]),
        evidence=evidence, owner_approval=approval,
    )


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
    family_capital_authorizations: tuple[FamilyCapitalAuthorization, ...] = ()

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
        authorizations = tuple(self.family_capital_authorizations)
        if any(not isinstance(item, FamilyCapitalAuthorization) for item in authorizations):
            raise PaperBookRegistryError("family capital authorizations must be typed records")
        if len({item.strategy_id for item in authorizations}) != len(authorizations):
            raise PaperBookRegistryError("family capital authorization strategy IDs must be unique")
        object.__setattr__(self, "family_capital_authorizations", tuple(sorted(
            authorizations, key=lambda item: item.strategy_id)))
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
            **({"family_capital_authorizations": [item.as_dict()
                                                   for item in self.family_capital_authorizations]}
               if self.family_capital_authorizations else {}),
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
        family_capital_authorizations=tuple(
            _authorization_from_dict(item) for item in value.get("family_capital_authorizations", [])),
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
            self._validate_successor(
                plan, allow_legacy_unverified="family_capital_authorizations" not in row["plan"])
            self._plans.append((plan, plan.sha256))

    @property
    def plans(self) -> tuple[FrozenPaperBookPlan, ...]:
        return tuple(plan for plan, _ in self._plans)

    def _validate_successor(self, plan: FrozenPaperBookPlan, *,
                            allow_legacy_unverified: bool = False) -> None:
        if any(existing.plan_id == plan.plan_id for existing, _ in self._plans):
            raise PaperBookRegistryError("allocation plan ID already exists")
        prior_plan = self._plans[-1][0] if self._plans else None
        if not self._plans:
            if plan.previous_plan_sha256 is not None:
                raise PaperBookRegistryError("first plan cannot name a predecessor")
            previous_hash = None
        else:
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
        old_family_capital: dict[str, Decimal] = {}
        for book in old_books.values():
            old_family_capital[book.strategy_id] = old_family_capital.get(book.strategy_id, Decimal(0)) + book.capital_inr
        added_family_capital: dict[str, Decimal] = {}
        for book in plan.books:
            if book.account_id not in old_books:
                added_family_capital[book.strategy_id] = (
                    added_family_capital.get(book.strategy_id, Decimal(0)) + book.capital_inr)
        authorization_by_strategy = {item.strategy_id: item for item in plan.family_capital_authorizations}
        if set(authorization_by_strategy) - {book.strategy_id for book in plan.books}:
            raise PaperBookRegistryError("family capital authorization names an ineligible strategy")
        prior_authorizations = {
            item.strategy_id: item
            for existing, _digest in self._plans
            for item in existing.family_capital_authorizations
        }
        approved_increase = Decimal(0)
        legacy_plan_without_auth = allow_legacy_unverified and not plan.family_capital_authorizations
        if not legacy_plan_without_auth:
            for book in plan.books:
                strategy_id = book.strategy_id
                added_capital = added_family_capital.get(strategy_id, Decimal(0))
                authorization = authorization_by_strategy.get(strategy_id)
                if added_capital == 0:
                    prior_authorization = prior_authorizations.get(strategy_id)
                    if prior_authorization is not None and authorization != prior_authorization:
                        raise PaperBookRegistryError("unchanged capital must retain its prior family authorization")
                    if prior_authorization is not None and authorization is None:
                        raise PaperBookRegistryError("family capital authorization cannot be removed")
                    continue
                if authorization is None:
                    if allow_legacy_unverified:
                        continue
                    raise PaperBookRegistryError(
                        "every new paper allocation needs exactly one family capital authorization")
                current = old_family_capital.get(strategy_id, Decimal(0))
                action = "CAPITAL_INCREASE" if current > 0 else "INITIAL_ALLOCATION"
                requested = current + added_capital
                if (authorization.action != action or authorization.current_capital_inr != current
                        or authorization.requested_capital_inr != requested):
                    raise PaperBookRegistryError("family capital authorization does not match the exact allocation change")
                try:
                    verdict = evaluate_family_action(
                        evidence=authorization.evidence, action=action,
                        current_capital_inr=current, requested_capital_inr=requested,
                        owner_approval=authorization.owner_approval, now=plan.registered_at,
                        execution_mode="PAPER",
                    )
                except GovernanceError as exc:
                    raise PaperBookRegistryError(f"family capital evidence is invalid: {exc}") from exc
                if not verdict.approved:
                    raise PaperBookRegistryError(
                        "family capital increase blocked: " + ", ".join(verdict.reasons))
                approved_increase += added_capital
        if (not legacy_plan_without_auth and prior_plan is not None
                and plan.total_capital_inr > prior_plan.total_capital_inr
                and plan.total_capital_inr - prior_plan.total_capital_inr > approved_increase):
            raise PaperBookRegistryError("unallocated capital budget increase lacks family authorization")
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

    def account_path(self, *, plan_id: str, account_id: str, root: Path) -> Path:
        """Return the only journal path assigned to an account in a registered plan."""
        plan = next((plan for plan, _ in self._plans if plan.plan_id == plan_id), None)
        if plan is None:
            raise PaperBookRegistryError("paper account paths require a registered plan")
        if not any(book.account_id == account_id for book in plan.books):
            raise PaperBookRegistryError("account ID is not allocated by the registered plan")
        path_id = hashlib.sha256(account_id.encode()).hexdigest()[:24]
        return Path(root).resolve() / f"{path_id}.jsonl"

    def open_accounts(self, *, plan_id: str, root: Path, fee_schedule: FeeSchedule) -> dict[str, PaperAccount]:
        plan = next((plan for plan, _ in self._plans if plan.plan_id == plan_id), None)
        if plan is None:
            raise PaperBookRegistryError("paper accounts require a registered plan")
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        accounts = {}
        for allocation in plan.books:
            accounts[allocation.strategy_id] = PaperAccount(
                self.account_path(plan_id=plan_id, account_id=allocation.account_id, root=root),
                account_id=allocation.account_id,
                initial_cash=allocation.capital_inr, currency="INR", fee_schedule=fee_schedule,
            )
        return accounts

    def submit_intent(self, *, plan_id: str, strategy_id: str, account: PaperAccount,
                      session_at: datetime, client_id: str, symbol: str, side: str,
                      quantity: int, limit: Decimal | None, reserve: Decimal,
                      family_evidence: FamilyEvidence | None = None,
                      owner_approval: Approval | None = None) -> None:
        plan, allocation = self.resolve(strategy_id=strategy_id, at=session_at)
        if plan.plan_id != plan_id:
            raise PaperBookRegistryError("session is not using the requested effective allocation plan")
        if account.account_id != allocation.account_id:
            raise PaperBookRegistryError("strategy cannot submit through another strategy's paper account")
        if family_evidence is None or family_evidence.family_id != strategy_id:
            raise PaperBookRegistryError("paper order requires matching family evidence")
        try:
            verdict = evaluate_family_action(
                evidence=family_evidence, action="AUTO_EXECUTION_ENABLE",
                current_capital_inr=allocation.capital_inr,
                requested_capital_inr=allocation.capital_inr,
                owner_approval=owner_approval, now=_aware(session_at, "session_at"),
                execution_mode="PAPER",
            )
        except GovernanceError as exc:
            raise PaperBookRegistryError(f"paper order family evidence is invalid: {exc}") from exc
        if not verdict.approved:
            raise PaperBookRegistryError("paper order family gate blocked: " + ", ".join(verdict.reasons))
        account.submit_intent(client_id=client_id, symbol=symbol, side=side,
                              quantity=quantity, limit=limit, reserve=reserve)

    def transfer(self, *_args, **_kwargs) -> None:
        raise PaperBookRegistryError("cash transfer is forbidden; register a new allocation plan effective in a future session")
