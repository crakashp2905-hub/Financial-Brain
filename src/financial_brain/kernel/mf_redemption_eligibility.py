"""Causal NAV eligibility for an accepted mutual-fund redemption."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone

from ..forward_edge.data_permissions import DataPermission, DataUse
from .fund_transactions import SchemeTransactionCalendar, TransactionKind
from .mf_order import MFOrder, MFOrderAcknowledgement, MFOrderEvidenceBasis, MFOrderKind, MFOrderState
from .trading_clock import IST


class MFRedemptionEligibilityError(ValueError):
    """A redemption cannot be assigned a NAV date without causal evidence."""


@dataclass(frozen=True, slots=True)
class EligibleRedemptionNAV:
    order_id: str
    transaction_at: datetime
    eligible_nav_date: date
    request_sha256: str
    acceptance_effective_at: datetime
    acceptance_received_at: datetime
    acceptance_basis: str
    acceptance_source_id: str | None
    acceptance_source_ref: str | None
    acceptance_source_sha256: str | None
    acceptance_reason: str | None
    permission_evidence: tuple[tuple[str, str, str, date | None, date | None, str, str | None], ...]
    official_acceptance: bool
    rule_source_ref: str
    rule_source_sha256: str
    rule_published_at: datetime
    cutoff_local: str
    calendar_source_ref: str
    calendar_source_sha256: str
    calendar_recorded_at: datetime
    decision_at: datetime
    event_sha256: str


def _aware(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise MFRedemptionEligibilityError(f"{label} must be timezone-aware")
    return value.astimezone(timezone.utc)


def resolve_redemption_eligible_nav(
    order: MFOrder,
    acceptance: MFOrderAcknowledgement,
    scheme_calendar: SchemeTransactionCalendar,
    *,
    decision_at: datetime,
    permissions: tuple[DataPermission, ...] = (),
) -> EligibleRedemptionNAV:
    """Derive an accepted redemption's eligible NAV date from its sourced cutoff.

    The function never accepts a caller-selected NAV date. The scheme calendar and
    effective rule must have been available at acceptance time, and the response must
    already be observed by decision time. Observed acceptance requires paper-use rights.
    """
    decision_time = _aware(decision_at, "decision_at")
    if order.transaction_type not in (MFOrderKind.REDEMPTION, MFOrderKind.SWITCH_OUT):
        raise MFRedemptionEligibilityError(
            "redemption NAV requires a REDEMPTION or SWITCH_OUT order"
        )
    if acceptance.order_id != order.order_id or acceptance.state is not MFOrderState.ACCEPTED:
        raise MFRedemptionEligibilityError("redemption requires acceptance of this exact order")
    if acceptance.effective_at < order.requested_at:
        raise MFRedemptionEligibilityError("acceptance precedes the redemption request")
    if acceptance.received_at > decision_time:
        raise MFRedemptionEligibilityError("accepted response is not known at decision time")
    permission_evidence: list[tuple[str, str, str, date | None, date | None, str, str | None]] = []
    if acceptance.basis is MFOrderEvidenceBasis.OBSERVED_EVIDENCE:
        effective_day = acceptance.effective_at.astimezone(IST).date()
        matches = [permission for permission in permissions if permission.allows(
            source_id=acceptance.source_id or "", use=DataUse.PAPER_SIMULATION, on=effective_day,
        )]
        if not matches:
            raise MFRedemptionEligibilityError(
                f"observed source {acceptance.source_id} lacks PAPER_SIMULATION permission"
            )
        permission_evidence = sorted([(
            item.source_id, item.use.value, item.status.value, item.effective_from,
            item.effective_through, item.evidence_ref, item.reviewer,
        ) for item in matches], key=lambda row: (
            row[0], row[1], row[2], row[3] or date.min, row[4] or date.max, row[5], row[6] or "",
        ))
    try:
        rule = scheme_calendar.rule_as_known(
            order.scheme_code, TransactionKind.REDEMPTION, transaction_at=acceptance.effective_at,
        )
        business_calendar = scheme_calendar.calendars[order.scheme_code]
        if business_calendar.recorded_at > acceptance.effective_at:
            raise MFRedemptionEligibilityError(
                "scheme business calendar was not known at redemption acceptance"
            )
        eligible = scheme_calendar.assign_nav_date(
            order.scheme_code, TransactionKind.REDEMPTION, received_at=acceptance.effective_at,
        )
    except MFRedemptionEligibilityError:
        raise
    except (KeyError, ValueError) as exc:
        raise MFRedemptionEligibilityError(f"cannot derive redemption NAV date: {exc}") from exc
    payload = {
        "order_id": order.order_id, "request_sha256": order.request_sha256,
        "acceptance": {
            "effective_at": acceptance.effective_at.isoformat(),
            "received_at": acceptance.received_at.isoformat(), "basis": acceptance.basis.value,
            "source_id": acceptance.source_id, "source_ref": acceptance.source_ref,
            "source_sha256": acceptance.source_sha256,
        },
        "permission_evidence": permission_evidence,
        "eligible_nav_date": eligible.isoformat(),
        "rule_source_ref": rule.source_ref, "rule_source_sha256": rule.source_sha256,
        "calendar_source_ref": business_calendar.source_ref,
        "calendar_source_sha256": business_calendar.source_sha256,
        "decision_at": decision_time.isoformat(),
    }
    digest = hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":"), default=str,
    ).encode("utf-8")).hexdigest()
    return EligibleRedemptionNAV(
        order_id=order.order_id, transaction_at=acceptance.effective_at,
        eligible_nav_date=eligible, request_sha256=order.request_sha256,
        acceptance_effective_at=acceptance.effective_at,
        acceptance_received_at=acceptance.received_at,
        acceptance_basis=acceptance.basis.value,
        acceptance_source_id=acceptance.source_id,
        acceptance_source_ref=acceptance.source_ref,
        acceptance_source_sha256=acceptance.source_sha256,
        acceptance_reason=acceptance.reason,
        permission_evidence=tuple(permission_evidence),
        official_acceptance=acceptance.is_official_acceptance,
        rule_source_ref=rule.source_ref, rule_source_sha256=rule.source_sha256,
        rule_published_at=rule.published_at, cutoff_local=rule.cutoff_local.isoformat(),
        calendar_source_ref=business_calendar.source_ref,
        calendar_source_sha256=business_calendar.source_sha256,
        calendar_recorded_at=business_calendar.recorded_at,
        decision_at=decision_time, event_sha256=digest,
    )


__all__ = ["EligibleRedemptionNAV", "MFRedemptionEligibilityError", "resolve_redemption_eligible_nav"]
