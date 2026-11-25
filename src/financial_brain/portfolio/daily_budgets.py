"""Separate daily portfolio turnover and commission caps from per-trade costs."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import StrEnum


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ZERO = Decimal("0")


class DailyBudgetError(ValueError):
    """Invalid policy, usage evidence or proposed order budget input."""


def _utc(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DailyBudgetError(f"{label} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _money(value: Decimal | str | int, label: str, *, positive: bool = False) -> Decimal:
    if isinstance(value, bool):
        raise DailyBudgetError(f"{label} must be a finite INR amount")
    try:
        amount = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise DailyBudgetError(f"{label} must be a finite INR amount") from exc
    if not amount.is_finite() or (amount <= _ZERO if positive else amount < _ZERO):
        relation = "positive" if positive else "non-negative"
        raise DailyBudgetError(f"{label} must be finite and {relation}")
    return amount


def _hash(value: str, label: str) -> None:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise DailyBudgetError(f"{label} must be a lowercase SHA-256")


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class DailyBudgetPolicy:
    scope_id: str
    session_date: date
    policy_version: str
    frozen_at: datetime
    maximum_turnover_inr: Decimal
    maximum_commission_inr: Decimal
    source_sha256: str

    def __post_init__(self) -> None:
        if not self.scope_id.strip() or not self.policy_version.strip():
            raise DailyBudgetError("scope and policy version are required")
        if not isinstance(self.session_date, date) or isinstance(self.session_date, datetime):
            raise DailyBudgetError("session_date must be a calendar date")
        frozen = _utc(self.frozen_at, "policy frozen_at")
        if frozen.date() > self.session_date:
            raise DailyBudgetError("daily budget policy cannot be frozen after its session")
        object.__setattr__(self, "frozen_at", frozen)
        object.__setattr__(self, "maximum_turnover_inr",
                           _money(self.maximum_turnover_inr, "maximum turnover"))
        object.__setattr__(self, "maximum_commission_inr",
                           _money(self.maximum_commission_inr, "maximum commission"))
        _hash(self.source_sha256, "policy source hash")

    def digest(self) -> str:
        return _digest({"scope_id": self.scope_id, "session_date": self.session_date.isoformat(),
                        "policy_version": self.policy_version, "frozen_at": self.frozen_at.isoformat(),
                        "maximum_turnover_inr": str(self.maximum_turnover_inr),
                        "maximum_commission_inr": str(self.maximum_commission_inr),
                        "source_sha256": self.source_sha256})


@dataclass(frozen=True, slots=True)
class ProposedOrderBudget:
    order_id: str
    scope_id: str
    session_date: date
    decision_at: datetime
    turnover_inr: Decimal
    commission_inr: Decimal
    priority: int
    source_sha256: str

    def __post_init__(self) -> None:
        if not self.order_id.strip() or not self.scope_id.strip():
            raise DailyBudgetError("order and scope identity are required")
        if not isinstance(self.session_date, date) or isinstance(self.session_date, datetime):
            raise DailyBudgetError("order session_date must be a calendar date")
        object.__setattr__(self, "decision_at", _utc(self.decision_at, "order decision_at"))
        object.__setattr__(self, "turnover_inr", _money(self.turnover_inr, "order turnover", positive=True))
        object.__setattr__(self, "commission_inr", _money(self.commission_inr, "order commission"))
        if isinstance(self.priority, bool) or not isinstance(self.priority, int):
            raise DailyBudgetError("order priority must be an integer")
        _hash(self.source_sha256, "order source hash")


@dataclass(frozen=True, slots=True)
class ReservedOrderBudget:
    order_id: str
    scope_id: str
    session_date: date
    reserved_at: datetime
    turnover_inr: Decimal
    commission_inr: Decimal
    source_sha256: str

    def __post_init__(self) -> None:
        if not self.order_id.strip() or not self.scope_id.strip():
            raise DailyBudgetError("reserved order and scope identity are required")
        if not isinstance(self.session_date, date) or isinstance(self.session_date, datetime):
            raise DailyBudgetError("reserved session_date must be a calendar date")
        object.__setattr__(self, "reserved_at", _utc(self.reserved_at, "reservation reserved_at"))
        object.__setattr__(self, "turnover_inr", _money(self.turnover_inr, "reserved turnover"))
        object.__setattr__(self, "commission_inr", _money(self.commission_inr, "reserved commission"))
        _hash(self.source_sha256, "reservation source hash")


class BudgetDisposition(StrEnum):
    ACCEPTED = "ACCEPTED"
    REJECTED_TURNOVER_CAP = "REJECTED_TURNOVER_CAP"
    REJECTED_COMMISSION_CAP = "REJECTED_COMMISSION_CAP"
    REJECTED_BOTH_CAPS = "REJECTED_BOTH_CAPS"
    HALTED_EXISTING_BUDGET_BREACH = "HALTED_EXISTING_BUDGET_BREACH"


@dataclass(frozen=True, slots=True)
class DailyBudgetDecision:
    order_id: str
    disposition: BudgetDisposition
    turnover_before_inr: Decimal
    turnover_after_inr: Decimal
    commission_before_inr: Decimal
    commission_after_inr: Decimal


@dataclass(frozen=True, slots=True)
class DailyBudgetAudit:
    policy_sha256: str
    scope_id: str
    session_date: date
    as_of: datetime
    turnover_used_inr: Decimal
    commission_used_inr: Decimal
    turnover_remaining_inr: Decimal
    commission_remaining_inr: Decimal
    input_evidence_sha256: str
    decisions: tuple[DailyBudgetDecision, ...]
    audit_sha256: str
    promotion_allowed: bool = False


def evaluate_daily_budgets(*, policy: DailyBudgetPolicy, as_of: datetime,
                           existing_reservations: tuple[ReservedOrderBudget, ...],
                           proposed_orders: tuple[ProposedOrderBudget, ...]
                           ) -> DailyBudgetAudit:
    """Reserve each whole proposed order only if both independent daily caps still fit.

    Existing reservations are counted before new proposals. New proposals are considered
    by descending caller-frozen priority then order ID. Each accepted order consumes both
    gross turnover and commission budget; a rejection consumes neither. This is a pure
    proposal and must be wrapped in a durable atomic reservation before concurrent use.
    """
    now = _utc(as_of, "as_of")
    if now.date() != policy.session_date:
        raise DailyBudgetError("as-of timestamp must be on the policy session date")
    if now <= policy.frozen_at:
        raise DailyBudgetError("as-of must follow the frozen budget policy")
    if len({row.order_id for row in existing_reservations}) != len(existing_reservations):
        raise DailyBudgetError("existing reservation order IDs must be unique")
    if len({row.order_id for row in proposed_orders}) != len(proposed_orders):
        raise DailyBudgetError("proposed order IDs must be unique")
    existing_ids = {row.order_id for row in existing_reservations}
    if existing_ids & {row.order_id for row in proposed_orders}:
        raise DailyBudgetError("an order cannot be both reserved and proposed")
    for row in existing_reservations:
        if row.scope_id != policy.scope_id or row.session_date != policy.session_date:
            raise DailyBudgetError("every reservation and proposal must match the budget scope and session")
        if (row.reserved_at.date() != policy.session_date or row.reserved_at < policy.frozen_at
                or row.reserved_at > now):
            raise DailyBudgetError("budget evidence timestamp must lie in the frozen session through as-of")
    for proposal in proposed_orders:
        if proposal.scope_id != policy.scope_id or proposal.session_date != policy.session_date:
            raise DailyBudgetError("every reservation and proposal must match the budget scope and session")
        if (proposal.decision_at.date() != policy.session_date or proposal.decision_at < policy.frozen_at
                or proposal.decision_at > now):
            raise DailyBudgetError("budget evidence timestamp must lie in the frozen session through as-of")

    turnover = sum((row.turnover_inr for row in existing_reservations), _ZERO)
    commission = sum((row.commission_inr for row in existing_reservations), _ZERO)
    existing_breach = (turnover > policy.maximum_turnover_inr
                       or commission > policy.maximum_commission_inr)
    decisions: list[DailyBudgetDecision] = []
    for order in sorted(proposed_orders, key=lambda row: (-row.priority, row.order_id)):
        before_turnover, before_commission = turnover, commission
        after_turnover = turnover + order.turnover_inr
        after_commission = commission + order.commission_inr
        if existing_breach:
            disposition = BudgetDisposition.HALTED_EXISTING_BUDGET_BREACH
        else:
            over_turnover = after_turnover > policy.maximum_turnover_inr
            over_commission = after_commission > policy.maximum_commission_inr
            if over_turnover and over_commission:
                disposition = BudgetDisposition.REJECTED_BOTH_CAPS
            elif over_turnover:
                disposition = BudgetDisposition.REJECTED_TURNOVER_CAP
            elif over_commission:
                disposition = BudgetDisposition.REJECTED_COMMISSION_CAP
            else:
                disposition = BudgetDisposition.ACCEPTED
                turnover, commission = after_turnover, after_commission
        decisions.append(DailyBudgetDecision(order.order_id, disposition, before_turnover,
                                             after_turnover, before_commission, after_commission))

    input_evidence = {
        "reservations": [{"order_id": row.order_id, "scope_id": row.scope_id,
                          "session_date": row.session_date.isoformat(),
                          "reserved_at": row.reserved_at.isoformat(),
                          "turnover_inr": str(row.turnover_inr),
                          "commission_inr": str(row.commission_inr),
                          "source_sha256": row.source_sha256}
                         for row in sorted(existing_reservations, key=lambda value: value.order_id)],
        "proposals": [{"order_id": row.order_id, "decision_at": row.decision_at.isoformat(),
                       "turnover_inr": str(row.turnover_inr),
                       "commission_inr": str(row.commission_inr), "priority": row.priority,
                       "source_sha256": row.source_sha256}
                      for row in sorted(proposed_orders, key=lambda value: value.order_id)],
    }
    evidence_digest = _digest(input_evidence)
    report = {
        "policy_sha256": policy.digest(), "scope_id": policy.scope_id,
        "session_date": policy.session_date.isoformat(), "as_of": now.isoformat(),
        "turnover_used_inr": str(turnover), "commission_used_inr": str(commission),
        "input_evidence_sha256": evidence_digest,
        "decisions": [{"order_id": row.order_id, "disposition": row.disposition.value,
                       "turnover_before_inr": str(row.turnover_before_inr),
                       "turnover_after_inr": str(row.turnover_after_inr),
                       "commission_before_inr": str(row.commission_before_inr),
                       "commission_after_inr": str(row.commission_after_inr)} for row in decisions],
    }
    return DailyBudgetAudit(policy.digest(), policy.scope_id, policy.session_date, now,
                            turnover, commission,
                            max(_ZERO, policy.maximum_turnover_inr - turnover),
                            max(_ZERO, policy.maximum_commission_inr - commission),
                            evidence_digest, tuple(decisions), _digest(report))


__all__ = ["BudgetDisposition", "DailyBudgetAudit", "DailyBudgetDecision", "DailyBudgetError",
           "DailyBudgetPolicy", "ProposedOrderBudget", "ReservedOrderBudget", "evaluate_daily_budgets"]
