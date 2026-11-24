"""Causal NAV eligibility for an accepted mutual-fund purchase.

The eligible date is derived from the accepted order, scheme-credit and
fund-utilization timestamps, and the effective scheme rule/calendar. Callers cannot
select a NAV date. This is a paper-domain calculation; it does not claim a connected
registrar or payment provider.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone

from ..forward_edge.data_permissions import DataPermission, DataUse
from .fund_transactions import SchemeTransactionCalendar, TransactionKind
from .trading_clock import IST
from .mf_order import (
    MFOrder,
    MFOrderAcknowledgement,
    MFOrderEvidenceBasis,
    MFOrderKind,
    MFOrderState,
)
from .mf_purchase_payment import (
    MFPurchasePaymentTimeline,
    PaymentEvidenceBasis,
    PaymentMilestoneKind,
)


class MFPurchaseEligibilityError(ValueError):
    """Purchase evidence cannot establish an eligible NAV date safely."""


@dataclass(frozen=True, slots=True)
class EligiblePurchaseNAV:
    order_id: str
    transaction_at: datetime
    eligible_nav_date: date
    accepted_effective_at: datetime
    accepted_received_at: datetime
    credited_to_scheme_at: datetime
    available_for_utilization_at: datetime
    request_sha256: str
    acceptance_receipt_sha256: str
    payment_timeline_sha256: str
    rule_source_ref: str
    rule_source_sha256: str
    rule_effective_from: date
    rule_effective_to: date | None
    rule_published_at: datetime
    cutoff_local: str
    before_cutoff_lag: int
    after_cutoff_lag: int
    closed_day_lag: int
    calendar_source_ref: str
    calendar_source_sha256: str
    calendar_recorded_at: datetime
    official_acceptance: bool
    decision_at: datetime
    event_sha256: str


def _aware(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise MFPurchaseEligibilityError(f"{label} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _require_permission(
    *, source_id: str, effective_at: datetime, permissions: tuple[DataPermission, ...],
) -> list[tuple[str, str, str, date | None, date | None, str, str | None]]:
    effective_date = effective_at.astimezone(IST).date()
    matches = [permission for permission in permissions if permission.allows(
        source_id=source_id, use=DataUse.PAPER_SIMULATION, on=effective_date,
    )]
    if not matches:
        raise MFPurchaseEligibilityError(
            f"observed source {source_id} lacks PAPER_SIMULATION permission"
        )
    rows = [(
        item.source_id, item.use.value, item.status.value, item.effective_from,
        item.effective_through, item.evidence_ref, item.reviewer,
    ) for item in matches]
    return sorted(rows, key=lambda row: (
        row[0], row[1], row[2], row[3] or date.min, row[4] or date.max,
        row[5], row[6] or "",
    ))


def resolve_purchase_eligible_nav(
    order: MFOrder,
    acceptance: MFOrderAcknowledgement,
    payment: MFPurchasePaymentTimeline,
    scheme_calendar: SchemeTransactionCalendar,
    *,
    decision_at: datetime,
    permissions: tuple[DataPermission, ...] = (),
) -> EligiblePurchaseNAV:
    """Derive a purchase NAV date from accepted-order and realized-funds evidence.

    The later of order acceptance, scheme credit, and utilization availability controls
    the effective transaction time. Each event must already be known at ``decision_at``.
    Missing clocks, unaccepted orders, unknown rule/calendar coverage, and unauthorized
    observed sources fail closed.
    """
    decision_time = _aware(decision_at, "decision_at")
    if order.transaction_type not in (MFOrderKind.PURCHASE, MFOrderKind.SWITCH_IN):
        raise MFPurchaseEligibilityError("eligible purchase NAV requires PURCHASE or SWITCH_IN")
    if acceptance.order_id != order.order_id or acceptance.state is not MFOrderState.ACCEPTED:
        raise MFPurchaseEligibilityError("eligible NAV requires acceptance of this exact order")
    if payment.order_id != order.order_id:
        raise MFPurchaseEligibilityError("payment timeline belongs to a different order")
    if acceptance.effective_at < order.requested_at:
        raise MFPurchaseEligibilityError("order acceptance precedes its request")
    if acceptance.received_at > decision_time:
        raise MFPurchaseEligibilityError("accepted-order receipt is not yet known")
    permission_evidence = []
    if acceptance.basis is MFOrderEvidenceBasis.OBSERVED_EVIDENCE:
        permission_evidence.extend(_require_permission(
            source_id=acceptance.source_id or "", effective_at=acceptance.effective_at,
            permissions=permissions,
        ))

    credited = payment.at(PaymentMilestoneKind.CREDITED_TO_SCHEME)
    available = payment.at(PaymentMilestoneKind.AVAILABLE_FOR_UTILIZATION)
    if credited is None or available is None:
        raise MFPurchaseEligibilityError(
            "scheme-credit and funds-available timestamps are both required"
        )
    for milestone in (credited, available):
        if milestone.received_at > decision_time:
            raise MFPurchaseEligibilityError("payment milestone is not yet known at decision time")
        if milestone.basis is PaymentEvidenceBasis.OBSERVED_EVIDENCE:
            permission_evidence.extend(_require_permission(
                source_id=milestone.source_id or "", effective_at=milestone.effective_at,
                permissions=permissions,
            ))

    transaction_at = max(acceptance.effective_at, credited.effective_at, available.effective_at)
    transaction_kind = (
        TransactionKind.PURCHASE
        if order.transaction_type is MFOrderKind.PURCHASE
        else TransactionKind.SWITCH_IN
    )
    try:
        rule = scheme_calendar.rule_as_known(
            order.scheme_code, transaction_kind, transaction_at=transaction_at,
        )
        business_calendar = scheme_calendar.calendars[order.scheme_code]
        if business_calendar.recorded_at > transaction_at:
            raise MFPurchaseEligibilityError(
                "scheme business calendar was not known at transaction time"
            )
        eligible_date = scheme_calendar.assign_nav_date(
            order.scheme_code, transaction_kind, received_at=transaction_at,
        )
    except MFPurchaseEligibilityError:
        raise
    except (KeyError, ValueError) as exc:
        raise MFPurchaseEligibilityError(f"cannot derive eligible NAV date: {exc}") from exc

    payload = {
        "order_id": order.order_id,
        "request_sha256": order.request_sha256,
        "acceptance": {
            "effective_at": acceptance.effective_at.isoformat(),
            "received_at": acceptance.received_at.isoformat(),
            "basis": acceptance.basis.value,
            "source_id": acceptance.source_id,
            "source_ref": acceptance.source_ref,
            "source_sha256": acceptance.source_sha256,
        },
        "payment_timeline_sha256": payment.event_sha256,
        "permission_evidence": permission_evidence,
        "transaction_at": transaction_at.isoformat(),
        "eligible_nav_date": eligible_date.isoformat(),
        "rule_source_ref": rule.source_ref,
        "rule_source_sha256": rule.source_sha256,
        "calendar_source_ref": business_calendar.source_ref,
        "calendar_source_sha256": business_calendar.source_sha256,
        "decision_at": decision_time.isoformat(),
    }
    digest = hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":"), default=str,
    ).encode("utf-8")).hexdigest()
    return EligiblePurchaseNAV(
        order_id=order.order_id,
        transaction_at=transaction_at,
        eligible_nav_date=eligible_date,
        accepted_effective_at=acceptance.effective_at,
        accepted_received_at=acceptance.received_at,
        credited_to_scheme_at=credited.effective_at,
        available_for_utilization_at=available.effective_at,
        request_sha256=order.request_sha256,
        acceptance_receipt_sha256=acceptance.source_sha256 or "",
        payment_timeline_sha256=payment.event_sha256,
        rule_source_ref=rule.source_ref,
        rule_source_sha256=rule.source_sha256,
        rule_effective_from=rule.effective_from,
        rule_effective_to=rule.effective_to,
        rule_published_at=rule.published_at,
        cutoff_local=rule.cutoff_local.isoformat(),
        before_cutoff_lag=rule.before_cutoff_lag,
        after_cutoff_lag=rule.after_cutoff_lag,
        closed_day_lag=rule.closed_day_lag,
        calendar_source_ref=business_calendar.source_ref,
        calendar_source_sha256=business_calendar.source_sha256,
        calendar_recorded_at=business_calendar.recorded_at,
        official_acceptance=acceptance.is_official_acceptance,
        decision_at=decision_time,
        event_sha256=digest,
    )


__all__ = [
    "EligiblePurchaseNAV", "MFPurchaseEligibilityError", "resolve_purchase_eligible_nav",
]
