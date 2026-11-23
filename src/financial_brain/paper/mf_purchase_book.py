"""Durable accepted-order, payment, NAV-pending, and MF purchase-allotment path."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from ..forward_edge.data_permissions import DataPermission, DataUse
from ..ingest.mfnav import as_known_on as mf_nav_as_known_on
from ..kernel.fund_transactions import SchemeTransactionCalendar
from ..kernel.mf_order import (
    MFOrder, MFOrderAcknowledgement, MFOrderEvidenceBasis, MFOrderKind, MFOrderState,
)
from ..kernel.mf_purchase_eligibility import (
    MFPurchaseEligibilityError, resolve_purchase_eligible_nav,
)
from ..kernel.mf_purchase_payment import (
    MFPurchasePaymentMilestone, MFPurchasePaymentTimeline,
    PaymentEvidenceBasis,
)
from ..kernel.trading_clock import IST
from ..providers.mf_scheme_terms import SchemeCommercialTerms


class MFPaperPurchaseError(ValueError):
    """An MF purchase cannot be reconciled without inventing evidence."""


def _aware(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise MFPaperPurchaseError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _hash(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _permission_evidence(permissions: tuple[DataPermission, ...], source: str,
                         effective_at: datetime) -> list[dict[str, Any]]:
    effective_date = effective_at.astimezone(IST).date()
    allowed = [item for item in permissions if item.allows(
        source_id=source, use=DataUse.PAPER_SIMULATION, on=effective_date,
    )]
    if not allowed:
        raise MFPaperPurchaseError(
            f"observed source {source} lacks effective PAPER_SIMULATION permission"
        )
    return [{
        "source_id": item.source_id, "use": item.use.value,
        "effective_from": item.effective_from.isoformat()
        if item.effective_from else None,
        "effective_through": item.effective_through.isoformat()
        if item.effective_through else None,
        "evidence_ref": item.evidence_ref, "reviewer": item.reviewer,
    } for item in sorted(allowed, key=lambda row: (row.source_id, row.evidence_ref))]


def record_payment_milestone(
    con,
    *,
    order_id: str,
    milestone: MFPurchasePaymentMilestone,
    permissions: tuple[DataPermission, ...] = (),
    now: datetime,
) -> dict[str, Any]:
    """Persist one immutable payment clock for a durable purchase order."""
    now_utc = _aware(now, "now")
    if now_utc < milestone.received_at:
        raise MFPaperPurchaseError("payment milestone cannot be stored before receipt")
    if not order_id.strip():
        raise MFPaperPurchaseError("order_id is required")
    if milestone.basis is PaymentEvidenceBasis.OBSERVED_EVIDENCE:
        permission = _permission_evidence(
            permissions, milestone.source_id or "", milestone.effective_at,
        )
    else:
        permission = []
    order_row = con.execute(
        "SELECT requested_at FROM mf_paper_orders WHERE order_id=?", [order_id],
    ).fetchone()
    if order_row is None:
        raise MFPaperPurchaseError("payment milestone has no persisted MF order")
    if milestone.effective_at < order_row[0]:
        raise MFPaperPurchaseError("payment milestone precedes its purchase request")
    if milestone.basis is PaymentEvidenceBasis.SIMULATED_ASSUMPTION and permission:
        raise AssertionError("simulated payment has no permission evidence")
    payload = {
        "order_id": order_id, "kind": milestone.kind.value,
        "effective_at": milestone.effective_at.isoformat(),
        "received_at": milestone.received_at.isoformat(),
        "basis": milestone.basis.value, "source_id": milestone.source_id,
        "source_ref": milestone.source_ref, "source_sha256": milestone.source_sha256,
        "permission_evidence": permission,
    }
    digest = _hash(payload)
    con.execute("BEGIN TRANSACTION")
    try:
        old = con.execute("""SELECT effective_at, received_at, basis, source_id,
            source_ref, source_sha256, permission_evidence_json, event_sha256
            FROM mf_paper_purchase_payment_events
            WHERE order_id=? AND milestone_kind=?""", [order_id, milestone.kind.value]).fetchone()
        if old is not None:
            if (old[0] != milestone.effective_at or old[1] != milestone.received_at
                    or old[2] != milestone.basis.value or old[3] != milestone.source_id
                    or old[4] != milestone.source_ref or old[5] != milestone.source_sha256
                    or json.loads(old[6]) != permission or old[7] != digest):
                raise MFPaperPurchaseError("payment milestone replay changed persisted evidence")
            con.execute("COMMIT")
            return {"order_id": order_id, "kind": milestone.kind.value,
                    "event_sha256": digest, "status": "already_recorded"}
        rows = con.execute("""SELECT milestone_kind, effective_at, received_at, basis,
            source_id, source_ref, source_sha256 FROM mf_paper_purchase_payment_events
            WHERE order_id=? ORDER BY sequence""", [order_id]).fetchall()
        timeline = MFPurchasePaymentTimeline(order_id, tuple(
            MFPurchasePaymentMilestone(
                kind=row[0], effective_at=row[1], received_at=row[2], basis=row[3],
                source_id=row[4], source_ref=row[5], source_sha256=row[6],
            ) for row in rows
        ) + (milestone,))
        # Validate the existing timeline plus the new milestone before persistence.
        del timeline
        sequence = con.execute("""SELECT COALESCE(MAX(sequence),0)+1
            FROM mf_paper_purchase_payment_events WHERE order_id=?""", [order_id]).fetchone()[0]
        con.execute("""INSERT INTO mf_paper_purchase_payment_events (
            order_id, sequence, milestone_kind, effective_at, received_at, basis,
            source_id, source_ref, source_sha256, permission_evidence_json, event_sha256)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""", [
                order_id, sequence, milestone.kind.value, milestone.effective_at,
                milestone.received_at, milestone.basis.value, milestone.source_id,
                milestone.source_ref, milestone.source_sha256,
                json.dumps(permission, sort_keys=True, separators=(",", ":")), digest,
            ])
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return {"order_id": order_id, "kind": milestone.kind.value,
            "event_sha256": digest, "status": "recorded"}


def _order_and_acceptance(con, order_id: str) -> tuple[MFOrder, MFOrderAcknowledgement]:
    row = con.execute("""SELECT order_id, account_id, folio_id, scheme_code,
        plan, option, transaction_type, requested_at, amount_inr, units,
        request_basis, request_sha256, current_state FROM mf_paper_orders
        WHERE order_id=?""", [order_id]).fetchone()
    if row is None:
        raise MFPaperPurchaseError("MF purchase order does not exist")
    if row[12] != MFOrderState.ACCEPTED.value:
        raise MFPaperPurchaseError("MF purchase requires a durable ACCEPTED order")
    order = MFOrder(
        order_id=row[0], account_id=row[1], folio_id=row[2], scheme_code=row[3],
        plan=row[4], option=row[5], transaction_type=MFOrderKind(row[6]),
        requested_at=row[7], amount_inr=row[8], units=row[9],
        request_basis=MFOrderEvidenceBasis(row[10]),
    )
    request_receipt = con.execute("""SELECT evidence_json FROM mf_paper_order_transitions
        WHERE order_id=? AND state='REQUESTED' ORDER BY sequence LIMIT 1""",
        [order_id]).fetchone()
    if request_receipt is None or json.loads(request_receipt[0]).get("request_sha256") != row[11]:
        raise MFPaperPurchaseError("persisted order request receipt does not reconcile")
    acceptance = con.execute("""SELECT effective_at, received_at, basis, source_id,
        source_ref, source_sha256, reason FROM mf_paper_order_transitions
        WHERE order_id=? AND state='ACCEPTED' ORDER BY sequence DESC LIMIT 1""",
        [order_id]).fetchone()
    if acceptance is None:
        raise MFPaperPurchaseError("ACCEPTED order is missing its durable transition receipt")
    return order, MFOrderAcknowledgement(
        order_id=order_id, state=MFOrderState.ACCEPTED, effective_at=acceptance[0],
        received_at=acceptance[1], basis=MFOrderEvidenceBasis(acceptance[2]),
        source_id=acceptance[3], source_ref=acceptance[4],
        source_sha256=acceptance[5], reason=acceptance[6],
    )


def _payment_timeline(con, order_id: str) -> MFPurchasePaymentTimeline:
    rows = con.execute("""SELECT milestone_kind, effective_at, received_at, basis,
        source_id, source_ref, source_sha256 FROM mf_paper_purchase_payment_events
        WHERE order_id=? ORDER BY sequence""", [order_id]).fetchall()
    return MFPurchasePaymentTimeline(order_id, tuple(
        MFPurchasePaymentMilestone(
            kind=row[0], effective_at=row[1], received_at=row[2], basis=row[3],
            source_id=row[4], source_ref=row[5], source_sha256=row[6],
        ) for row in rows
    ))


def resolve_eligible_nav(
    con,
    *,
    order_id: str,
    scheme_calendar: SchemeTransactionCalendar,
    permissions: tuple[DataPermission, ...],
    decision_at: datetime,
    now: datetime,
) -> dict[str, Any]:
    """Freeze NAV eligibility in NAV_PENDING once accepted order and funds are known."""
    decision = _aware(decision_at, "decision_at")
    recorded = _aware(now, "now")
    if recorded < decision:
        raise MFPaperPurchaseError("eligibility record time precedes decision time")
    order, acceptance = _order_and_acceptance(con, order_id)
    timeline = _payment_timeline(con, order_id)
    try:
        result = resolve_purchase_eligible_nav(
            order, acceptance, timeline, scheme_calendar,
            decision_at=decision, permissions=permissions,
        )
    except MFPurchaseEligibilityError as exc:
        raise MFPaperPurchaseError(str(exc)) from exc
    evidence = json.dumps(result.__dict__ if hasattr(result, "__dict__") else {
        field: getattr(result, field) for field in result.__dataclass_fields__
    }, sort_keys=True, separators=(",", ":"), default=str)
    con.execute("BEGIN TRANSACTION")
    try:
        existing = con.execute("""SELECT state, eligible_nav_date, transaction_at,
            decision_at, eligibility_sha256 FROM mf_paper_purchase_eligibility
            WHERE order_id=?""", [order_id]).fetchone()
        if existing is not None:
            if (existing[4] != result.event_sha256
                    or existing[1] != result.eligible_nav_date
                    or existing[2] != result.transaction_at):
                raise MFPaperPurchaseError("purchase NAV eligibility is immutable once recorded")
            con.execute("COMMIT")
            return {"order_id": order_id, "state": existing[0],
                    "eligible_nav_date": existing[1], "transaction_at": existing[2],
                    "eligibility_sha256": existing[4], "status": "already_resolved"}
        con.execute("""INSERT INTO mf_paper_purchase_eligibility (
            order_id, state, eligible_nav_date, transaction_at, decision_at,
            evidence_json, eligibility_sha256, created_at, updated_at)
            VALUES (?, 'NAV_PENDING', ?, ?, ?, ?, ?, ?, ?)""", [
                order_id, result.eligible_nav_date, result.transaction_at, decision,
                evidence, result.event_sha256, recorded, recorded,
            ])
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return {"order_id": order_id, "state": "NAV_PENDING",
            "eligible_nav_date": result.eligible_nav_date,
            "transaction_at": result.transaction_at,
            "eligibility_sha256": result.event_sha256, "status": "resolved"}


def apply_allotment(
    con,
    *,
    order_id: str,
    nav_observation_id: str,
    nav_per_unit: Decimal | str | int,
    terms: SchemeCommercialTerms,
    permissions: tuple[DataPermission, ...],
    decision_at: datetime,
    allotted_at: datetime,
    now: datetime,
    lock_in_until=None,
) -> dict[str, Any]:
    """Use only the frozen eligible-day NAV; otherwise keep the order NAV_PENDING."""
    decision = _aware(decision_at, "decision_at")
    allotted = _aware(allotted_at, "allotted_at")
    recorded = _aware(now, "now")
    nav = Decimal(str(nav_per_unit))
    if not nav.is_finite() or nav <= 0:
        raise MFPaperPurchaseError("NAV must be positive and finite")
    if not nav_observation_id.strip():
        raise MFPaperPurchaseError("NAV observation ID is required")
    if decision > allotted or allotted > recorded:
        raise MFPaperPurchaseError("allotment clocks are out of order")
    con.execute("BEGIN TRANSACTION")
    try:
        eligibility = con.execute("""SELECT state, eligible_nav_date, decision_at,
            eligibility_sha256, nav_observation_id, nav_per_unit, units_allotted
            FROM mf_paper_purchase_eligibility WHERE order_id=?""", [order_id]).fetchone()
        if eligibility is None:
            raise MFPaperPurchaseError("purchase has no frozen eligible NAV date")
        if eligibility[0] == "CANCELLED":
            raise MFPaperPurchaseError("purchase was cancelled before allotment")
        if decision < eligibility[2]:
            raise MFPaperPurchaseError("allotment decision precedes NAV eligibility record")
        if eligibility[0] == "ALLOTTED":
            if eligibility[4] != nav_observation_id or Decimal(eligibility[5]) != nav:
                raise MFPaperPurchaseError("allotment replay changed NAV evidence")
            con.execute("COMMIT")
            return {"order_id": order_id, "state": "ALLOTTED",
                    "eligible_nav_date": eligibility[1], "units": Decimal(eligibility[6]),
                    "status": "already_allotted"}
        order, _acceptance = _order_and_acceptance(con, order_id)
        if order.transaction_type not in (MFOrderKind.PURCHASE, MFOrderKind.SWITCH_IN) \
                or order.amount_inr is None:
            raise MFPaperPurchaseError("paper allotment requires a PURCHASE or SWITCH_IN order")
        observation = mf_nav_as_known_on(
            con, order.scheme_code, eligibility[1], known_at=decision,
        )
        if (observation is None or observation["observation_id"] != nav_observation_id
                or Decimal(str(observation["nav"])) != nav):
            raise MFPaperPurchaseError(
                "NAV_PENDING: exact eligible-date NAV is not yet available as known"
            )
        permission = _permission_evidence(
            permissions, "AMFI", decision.astimezone(IST),
        )
        evidence = {
            "order_id": order_id, "eligibility_sha256": eligibility[3],
            "eligible_nav_date": eligibility[1].isoformat(),
            "nav_observation_id": nav_observation_id,
            "nav_row_sha256": observation["row_sha256"],
            "nav_available_at": observation["available_at"].isoformat(),
            "nav_source_published_at": (observation["source_published_at"].isoformat()
                                        if observation["source_published_at"] else None),
            "permission_evidence": permission, "terms_record_id": terms.record_id,
        }
        allotment_hash = _hash({"evidence": evidence, "nav": str(nav),
                                "amount": str(order.amount_inr)})
        from .mf_lot_book import subscribe
        subscription = subscribe(
            con, account_id=order.account_id, folio_id=order.folio_id,
            event_id=f"mf-purchase:{order_id}",
            lot_id=f"mf-purchase-lot:{order_id}", scheme_code=order.scheme_code,
            plan=order.plan, option=order.option, isin=terms.isin,
            allotted_on=eligibility[1], lock_in_until=lock_in_until,
            investment_inr=order.amount_inr, nav_per_unit=nav,
            exit_loads=terms.exit_loads,
            exit_loads_sha256=terms.source_sha256 if terms.exit_loads is not None else None,
            terms=terms, permissions=permissions, decision_at=decision, now=allotted,
            funding_account_code=(
                "SIP_PAYMENT_PENDING" if order.transaction_type is MFOrderKind.SWITCH_IN
                else "CASH"
            ),
            _transaction_managed=True,
        )
        con.execute("""UPDATE mf_paper_purchase_eligibility SET state='ALLOTTED',
            nav_observation_id=?, nav_per_unit=?, units_allotted=?, allotment_sha256=?,
            updated_at=? WHERE order_id=? AND state='NAV_PENDING'""", [
                nav_observation_id, nav, subscription["units"], allotment_hash,
                recorded, order_id,
            ])
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return {"order_id": order_id, "state": "ALLOTTED",
            "eligible_nav_date": eligibility[1], "units": subscription["units"],
            "status": "allotted"}


__all__ = [
    "MFPaperPurchaseError", "apply_allotment", "record_payment_milestone",
    "resolve_eligible_nav",
]
