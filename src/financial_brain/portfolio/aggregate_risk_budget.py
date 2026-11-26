"""Fail-closed account-wide loss-risk budget over the canonical paper ledger."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping

from financial_brain.portfolio.canonical_ledger import (
    AssetClass,
    CanonicalPortfolioLedger,
    EventKind,
    PortfolioEvent,
)
from financial_brain.forward_edge.order_journal import OrderJournal


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ZERO = Decimal("0")


class AggregateRiskError(ValueError):
    """Invalid risk-budget policy or desk evidence contract."""


def _utc(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise AggregateRiskError(f"{label} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _decimal(value: Decimal | str | int, label: str) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise AggregateRiskError(f"{label} must be finite non-negative INR") from exc
    if not result.is_finite() or result < 0:
        raise AggregateRiskError(f"{label} must be finite non-negative INR")
    return result


def _check_hash(value: str, label: str) -> None:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise AggregateRiskError(f"{label} must be a lowercase SHA-256")


def _digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         allow_nan=False, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class AggregateRiskDisposition(StrEnum):
    PERMITTED = "PERMITTED"
    DENIED_RISK_BUDGET = "DENIED_RISK_BUDGET"
    BLOCKED_LEDGER_UNCERTAIN = "BLOCKED_LEDGER_UNCERTAIN"
    BLOCKED_RISK_EVIDENCE = "BLOCKED_RISK_EVIDENCE"
    BLOCKED_ORDER_STATE_UNCERTAIN = "BLOCKED_ORDER_STATE_UNCERTAIN"


@dataclass(frozen=True, slots=True)
class AggregateRiskPolicy:
    account_id: str
    policy_version: str
    frozen_at: datetime
    maximum_loss_risk_inr: Decimal
    active_desks: tuple[str, ...]
    maximum_mark_age_seconds: int
    source_sha256: str

    def __post_init__(self) -> None:
        if not self.account_id.strip() or not self.policy_version.strip():
            raise AggregateRiskError("account ID and policy version are required")
        desks = tuple(sorted(set(self.active_desks)))
        if not desks or any(not desk.strip() for desk in desks):
            raise AggregateRiskError("active desks must be non-empty and unique")
        if isinstance(self.maximum_mark_age_seconds, bool) or not isinstance(self.maximum_mark_age_seconds, int) or self.maximum_mark_age_seconds < 1:
            raise AggregateRiskError("maximum mark age must be a positive integer number of seconds")
        object.__setattr__(self, "active_desks", desks)
        object.__setattr__(self, "frozen_at", _utc(self.frozen_at, "policy frozen_at"))
        object.__setattr__(self, "maximum_loss_risk_inr",
                           _decimal(self.maximum_loss_risk_inr, "maximum loss risk"))
        _check_hash(self.source_sha256, "risk policy source hash")

    def digest(self) -> str:
        return _digest({"account_id": self.account_id, "policy_version": self.policy_version,
                        "frozen_at": self.frozen_at.isoformat(),
                        "maximum_loss_risk_inr": str(self.maximum_loss_risk_inr),
                        "active_desks": self.active_desks,
                        "maximum_mark_age_seconds": self.maximum_mark_age_seconds,
                        "source_sha256": self.source_sha256})


@dataclass(frozen=True, slots=True)
class DeskRiskEstimate:
    desk_id: str
    as_of: datetime
    current_loss_risk_inr: Decimal
    proposed_incremental_loss_risk_inr: Decimal
    evidence_sha256: str

    def __post_init__(self) -> None:
        if not self.desk_id.strip():
            raise AggregateRiskError("desk ID is required")
        object.__setattr__(self, "as_of", _utc(self.as_of, "desk risk as_of"))
        object.__setattr__(self, "current_loss_risk_inr",
                           _decimal(self.current_loss_risk_inr, "current loss risk"))
        object.__setattr__(self, "proposed_incremental_loss_risk_inr",
                           _decimal(self.proposed_incremental_loss_risk_inr, "incremental loss risk"))
        _check_hash(self.evidence_sha256, "desk risk evidence hash")


@dataclass(frozen=True, slots=True)
class AggregateRiskDecision:
    disposition: AggregateRiskDisposition
    reason: str
    account_id: str
    as_of: datetime
    current_loss_risk_inr: Decimal | None
    projected_loss_risk_inr: Decimal | None
    maximum_loss_risk_inr: Decimal
    remaining_loss_risk_inr: Decimal | None
    desk_risk_inr: Mapping[str, Decimal]
    ledger_events_sha256: str | None
    ledger_snapshot_sha256: str | None
    order_audit_sha256: str | None
    unresolved_order_ids: tuple[str, ...]
    policy_sha256: str
    risk_evidence_sha256: str | None
    decision_sha256: str
    order_permitted: bool


def evaluate_aggregate_risk_budget(*, ledger: CanonicalPortfolioLedger,
                                   order_journal: OrderJournal,
                                   policy: AggregateRiskPolicy, as_of: datetime,
                                   desk_risks: tuple[DeskRiskEstimate, ...]
                                   ) -> AggregateRiskDecision:
    """Check all active desks against one loss-risk cap and a complete fresh ledger.

    Any exception or uncertain canonical snapshot/order journal blocks permission. Desk risk
    estimates remain sourced inputs; this wrapper does not calibrate their scenarios or infer
    missing loss exposure. Universal OMS wiring remains a separate integration requirement.
    """
    now = _utc(as_of, "as_of")
    desk_map = {row.desk_id: row for row in desk_risks}
    evidence_hash = _digest({desk: row.evidence_sha256 for desk, row in sorted(desk_map.items())})
    current: Decimal | None = None
    projected: Decimal | None = None
    desk_totals: dict[str, Decimal] = {}
    events_hash: str | None = None
    snapshot_hash: str | None = None
    order_audit_hash: str | None = None
    unresolved_order_ids: tuple[str, ...] = ()
    disposition = AggregateRiskDisposition.BLOCKED_RISK_EVIDENCE
    reason = "desk risk evidence does not exactly cover the active-desk policy"

    if (len(desk_map) != len(desk_risks) or set(desk_map) != set(policy.active_desks)
            or any(row.as_of != now for row in desk_risks) or now <= policy.frozen_at
            or any(row.current_loss_risk_inr + row.proposed_incremental_loss_risk_inr
                   > policy.maximum_loss_risk_inr for row in desk_risks)):
        return _decision(disposition, reason, policy, now, current, projected, desk_totals,
                         events_hash, snapshot_hash, evidence_hash, order_audit_hash,
                         unresolved_order_ids)

    for desk, row in desk_map.items():
        desk_totals[desk] = row.current_loss_risk_inr + row.proposed_incremental_loss_risk_inr
    current = sum((row.current_loss_risk_inr for row in desk_map.values()), _ZERO)
    projected = current + sum((row.proposed_incremental_loss_risk_inr for row in desk_map.values()), _ZERO)

    try:
        order_audit = order_journal.audit_state(now)
        order_audit_hash = str(order_audit["sha256"])
        unresolved_order_ids = tuple(order_audit["ambiguous_order_ids"])
        if unresolved_order_ids:
            return _decision(
                AggregateRiskDisposition.BLOCKED_ORDER_STATE_UNCERTAIN,
                "ambiguous pending orders exceed the zero-tolerance account gate: "
                + ",".join(unresolved_order_ids), policy, now, current, projected, desk_totals,
                events_hash, snapshot_hash, evidence_hash, order_audit_hash, unresolved_order_ids)
    except Exception as exc:  # journal errors must never permit risk
        return _decision(
            AggregateRiskDisposition.BLOCKED_ORDER_STATE_UNCERTAIN,
            f"ORDER_AUDIT_FAILED:{type(exc).__name__}", policy, now, current, projected,
            desk_totals, events_hash, snapshot_hash, evidence_hash, order_audit_hash,
            unresolved_order_ids)

    try:
        events = ledger.events(policy.account_id, as_of=now.isoformat())
        if not events:
            raise AggregateRiskError("canonical ledger has no events for the account")
        verify = ledger.verify_import([_portfolio_event(row) for row in events])
        if verify != {"verified": len(events), "missing": 0, "mismatched": 0}:
            raise AggregateRiskError("canonical ledger event payload hash verification failed")
        snapshot = ledger.snapshot(policy.account_id, as_of=now.isoformat())
        events_hash = _digest(events)
        snapshot_hash = _digest(snapshot)
        reasons = _ledger_uncertainties(snapshot, policy, now)
    except Exception as exc:  # ledger read/validation errors must never permit risk
        reasons = [f"LEDGER_READ_FAILED:{type(exc).__name__}"]
    if reasons:
        disposition = AggregateRiskDisposition.BLOCKED_LEDGER_UNCERTAIN
        return _decision(disposition, ";".join(reasons), policy, now, current, projected,
                         desk_totals, events_hash, snapshot_hash, evidence_hash,
                         order_audit_hash, unresolved_order_ids)

    if projected > policy.maximum_loss_risk_inr:
        disposition = AggregateRiskDisposition.DENIED_RISK_BUDGET
        reason = "projected aggregate loss risk exceeds the account-wide cap"
    else:
        disposition = AggregateRiskDisposition.PERMITTED
        reason = "all active desks fit the account-wide loss-risk cap and ledger freshness gates"
    return _decision(disposition, reason, policy, now, current, projected, desk_totals,
                     events_hash, snapshot_hash, evidence_hash, order_audit_hash,
                     unresolved_order_ids)


def _ledger_uncertainties(snapshot: object, policy: AggregateRiskPolicy,
                          as_of: datetime) -> list[str]:
    if not isinstance(snapshot, dict) or snapshot.get("account_id") != policy.account_id:
        return ["ACCOUNT_SNAPSHOT_MISMATCH"]
    reasons: list[str] = []
    if snapshot.get("opening_balance_supplied") is not True:
        reasons.append("OPENING_BALANCE_MISSING")
    if snapshot.get("cash_scope") != "COMPLETE_WITH_OPENING_BALANCE":
        reasons.append("CASH_SCOPE_INCOMPLETE")
    if snapshot.get("projection_complete") is not True:
        reasons.append("POSITION_PROJECTION_INCOMPLETE")
    if snapshot.get("unresolved_positions"):
        reasons.append("UNRESOLVED_POSITIONS")
    cash = snapshot.get("cash")
    if not isinstance(cash, dict) or not cash or set(cash) != {"INR"}:
        reasons.append("CASH_CURRENCY_UNSUPPORTED_OR_MISSING")
    positions = snapshot.get("positions")
    if not isinstance(positions, list):
        reasons.append("POSITION_SNAPSHOT_MISSING")
        return reasons
    observed_desks: set[str] = set()
    for row in positions:
        if not isinstance(row, dict):
            reasons.append("MALFORMED_POSITION_ROW")
            continue
        currency = row.get("currency")
        if currency != "INR":
            reasons.append("POSITION_CURRENCY_UNSUPPORTED")
        if row.get("quantity") != 0:
            mark_at = row.get("mark_at")
            try:
                marked = datetime.fromisoformat(str(mark_at).replace("Z", "+00:00"))
                if marked.tzinfo is None or marked.utcoffset() is None:
                    raise ValueError("naive mark time")
                age = (as_of - marked.astimezone(timezone.utc)).total_seconds()
                if age < 0 or age > policy.maximum_mark_age_seconds:
                    reasons.append("POSITION_MARK_STALE_OR_FUTURE")
            except (TypeError, ValueError):
                reasons.append("POSITION_MARK_TIME_INVALID")
        sleeves = row.get("sleeves")
        if not isinstance(sleeves, list) or any(not isinstance(value, str) for value in sleeves):
            reasons.append("POSITION_SLEEVE_IDENTITY_MISSING")
        else:
            observed_desks.update(sleeves)
    if not observed_desks <= set(policy.active_desks):
        reasons.append("POSITION_SLEEVE_NOT_IN_ACTIVE_DESK_POLICY")
    gate = snapshot.get("pnl_promotion_gate")
    if not isinstance(gate, dict) or gate.get("blockers"):
        reasons.append("LEDGER_PROMOTION_BLOCKERS_PRESENT")
    return reasons


def _portfolio_event(row: dict[str, object]) -> PortfolioEvent:
    """Rehydrate a normalized ledger event so its stored payload hash can be checked."""
    metadata = row.get("metadata")
    return PortfolioEvent(
        account_id=str(row["account_id"]), sleeve_id=str(row["sleeve_id"]),
        source_system=str(row["source_system"]), source_event_id=str(row["source_event_id"]),
        source_sha256=str(row["source_sha256"]), event_kind=EventKind(str(row["event_kind"])),
        occurred_at=str(row["occurred_at"]), currency=str(row["currency"]),
        asset_class=AssetClass(str(row["asset_class"])) if row.get("asset_class") is not None else None,
        instrument_id=str(row["instrument_id"]) if row.get("instrument_id") is not None else None,
        side=str(row["side"]) if row.get("side") is not None else None,
        quantity=str(row["quantity"]) if row.get("quantity") is not None else None,
        price=str(row["price"]) if row.get("price") is not None else None,
        multiplier=str(row["multiplier"]),
        cash_delta=str(row["cash_delta"]) if row.get("cash_delta") is not None else None,
        evidence_class=str(row["evidence_class"]),
        metadata=metadata if isinstance(metadata, dict) else {},
    )


def _decision(disposition: AggregateRiskDisposition, reason: str, policy: AggregateRiskPolicy,
              as_of: datetime, current: Decimal | None, projected: Decimal | None,
              desk_risk: Mapping[str, Decimal], events_hash: str | None, snapshot_hash: str | None,
              evidence_hash: str | None, order_audit_hash: str | None,
              unresolved_order_ids: tuple[str, ...]) -> AggregateRiskDecision:
    remaining = max(_ZERO, policy.maximum_loss_risk_inr - projected) if projected is not None else None
    body = {"disposition": disposition.value, "reason": reason, "account_id": policy.account_id,
            "as_of": as_of.isoformat(), "current_loss_risk_inr": current,
            "projected_loss_risk_inr": projected, "maximum_loss_risk_inr": policy.maximum_loss_risk_inr,
            "remaining_loss_risk_inr": remaining, "desk_risk_inr": dict(desk_risk),
            "ledger_events_sha256": events_hash, "ledger_snapshot_sha256": snapshot_hash,
            "order_audit_sha256": order_audit_hash,
            "unresolved_order_ids": unresolved_order_ids,
            "policy_sha256": policy.digest(), "risk_evidence_sha256": evidence_hash,
            "order_permitted": disposition is AggregateRiskDisposition.PERMITTED}
    return AggregateRiskDecision(disposition, reason, policy.account_id, as_of, current, projected,
                                 policy.maximum_loss_risk_inr, remaining,
                                 MappingProxyType(dict(sorted(desk_risk.items()))), events_hash,
                                 snapshot_hash, order_audit_hash, unresolved_order_ids,
                                 policy.digest(), evidence_hash, _digest(body),
                                 disposition is AggregateRiskDisposition.PERMITTED)


__all__ = ["AggregateRiskDecision", "AggregateRiskDisposition", "AggregateRiskError",
           "AggregateRiskPolicy", "DeskRiskEstimate", "evaluate_aggregate_risk_budget"]
