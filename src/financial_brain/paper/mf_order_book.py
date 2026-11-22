"""Durable MF paper order requests and evidence-bound lifecycle transitions."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from ..forward_edge.data_permissions import DataPermission, DataUse
from ..kernel.trading_clock import IST
from ..kernel.mf_order import (
    MFOrder, MFOrderAcknowledgement, MFOrderEvidenceBasis, MFOrderKind, MFOrderState,
    platform_valid_through,
)


class MFPaperOrderBookError(ValueError):
    """Invalid, unauthorized, or conflicting durable paper-order operation."""


def _aware(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise MFPaperOrderBookError(f"{label} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _append_transition(
    con, *, order_id: str, sequence: int, state: MFOrderState,
    effective_at: datetime, received_at: datetime, basis: str,
    source_id: str | None, source_ref: str | None, source_sha256: str | None,
    reason: str | None, evidence: dict[str, Any],
) -> str:
    event_hash = _hash({
        "order_id": order_id, "sequence": sequence, "state": state.value,
        "effective_at": effective_at, "received_at": received_at,
        "basis": basis, "source_id": source_id, "source_ref": source_ref,
        "source_sha256": source_sha256, "reason": reason, "evidence": evidence,
    })
    con.execute("""INSERT INTO mf_paper_order_transitions (
        order_id, sequence, state, effective_at, received_at, basis, source_id,
        source_ref, source_sha256, reason, event_sha256, evidence_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        [order_id, sequence, state.value, effective_at, received_at, basis,
         source_id, source_ref, source_sha256, reason, event_hash,
         json.dumps(evidence, sort_keys=True, separators=(",", ":"), default=str)])
    return event_hash


def submit_order(
    con,
    order: MFOrder,
    *,
    valid_business_days: int,
    calendar,
    now: datetime,
) -> dict[str, Any]:
    """Persist immutable request and initial transition idempotently."""
    observed_at = _aware(now, "now")
    if observed_at < order.requested_at:
        raise MFPaperOrderBookError("request cannot be persisted before requested_at")
    valid_through = platform_valid_through(
        order, valid_business_days=valid_business_days, calendar=calendar,
    )
    if valid_through < order.requested_at.astimezone(IST).date():
        raise MFPaperOrderBookError("valid_through precedes request date")
    evidence = {"request_sha256": order.request_sha256,
                "valid_through": valid_through.isoformat()}
    con.execute("BEGIN TRANSACTION")
    try:
        if con.execute("SELECT 1 FROM mf_paper_accounts WHERE account_id=?",
                        [order.account_id]).fetchone() is None:
            raise MFPaperOrderBookError("paper account does not exist")
        old = con.execute("""SELECT request_sha256, valid_through, current_state
            FROM mf_paper_orders WHERE order_id=?""", [order.order_id]).fetchone()
        if old is not None:
            if old[0] != order.request_sha256 or old[1] != valid_through:
                raise MFPaperOrderBookError("order ID was reused for different request data")
            con.execute("COMMIT")
            return {"order_id": order.order_id, "state": old[2], "status": "already_submitted"}
        con.execute("""INSERT INTO mf_paper_orders (
            order_id, account_id, folio_id, scheme_code, plan, option,
            transaction_type, requested_at, amount_inr, units, request_basis,
            request_sha256, valid_through, current_state, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'REQUESTED',?,?)""",
            [order.order_id, order.account_id, order.folio_id, order.scheme_code,
             order.plan, order.option, order.transaction_type.value,
             order.requested_at, order.amount_inr, order.units,
             order.request_basis.value, order.request_sha256, valid_through,
             observed_at, observed_at])
        _append_transition(
            con, order_id=order.order_id, sequence=1, state=MFOrderState.REQUESTED,
            effective_at=order.requested_at, received_at=observed_at,
            basis=order.request_basis.value, source_id=None, source_ref=None,
            source_sha256=None, reason=None, evidence=evidence,
        )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return {"order_id": order.order_id, "state": "REQUESTED", "status": "submitted"}


def record_response(
    con,
    response: MFOrderAcknowledgement,
    *,
    permissions: tuple[DataPermission, ...] = (),
    now: datetime,
) -> dict[str, Any]:
    """Persist response with source permission for observed paper-simulation data."""
    observed_at = _aware(now, "now")
    if observed_at < response.received_at:
        raise MFPaperOrderBookError("response cannot be persisted before it was received")
    allowed: tuple[tuple[Any, ...], ...] = ()
    if response.basis is MFOrderEvidenceBasis.OBSERVED_EVIDENCE:
        effective_date = response.effective_at.astimezone(IST).date()
        matches = [permission for permission in permissions if permission.allows(
            source_id=response.source_id or "", use=DataUse.PAPER_SIMULATION,
            on=effective_date,
        )]
        if not matches:
            raise MFPaperOrderBookError("observed order response lacks PAPER_SIMULATION permission")
        allowed_rows = [(
            item.source_id, item.evidence_ref, item.reviewer,
            item.effective_from, item.effective_through,
        ) for item in matches]
        allowed = tuple(sorted(allowed_rows, key=lambda item: (item[0], item[1])))

    con.execute("BEGIN TRANSACTION")
    try:
        row = con.execute("""SELECT requested_at, current_state, valid_through,
            account_id, folio_id, scheme_code, plan, option, transaction_type, units,
            amount_inr
            FROM mf_paper_orders
            WHERE order_id=?""", [response.order_id]).fetchone()
        if row is None:
            raise MFPaperOrderBookError("paper order does not exist")
        (requested_at, current, valid_through, account_id, folio_id, scheme_code,
         plan, option, transaction_type, order_units, amount_inr) = row
        if response.effective_at < requested_at:
            raise MFPaperOrderBookError("response effective time precedes order request")
        if response.effective_at.astimezone(IST).date() > valid_through:
            raise MFPaperOrderBookError("response effective time is outside platform business-day validity")
        prior = MFOrderState(current)
        evidence = {"state": response.state.value, "basis": response.basis.value,
                    "permission_evidence": [list(item) for item in allowed]}
        last = con.execute("""SELECT state, effective_at, received_at, basis,
            source_id, source_ref, source_sha256, reason, evidence_json
            FROM mf_paper_order_transitions WHERE order_id=? ORDER BY sequence DESC LIMIT 1""",
            [response.order_id]).fetchone()
        if (last is not None and last[0] == response.state.value
                and last[1] == response.effective_at and last[2] == response.received_at
                and last[3] == response.basis.value and last[4] == response.source_id
                and last[5] == response.source_ref and last[6] == response.source_sha256
                and last[7] == response.reason
                and json.loads(last[8]) == evidence):
            con.execute("COMMIT")
            return {"order_id": response.order_id, "state": response.state.value,
                    "status": "already_recorded",
                    "is_official_acceptance": response.is_official_acceptance}
        if prior in (MFOrderState.ACCEPTED, MFOrderState.REJECTED):
            raise MFPaperOrderBookError("accepted or rejected order is terminal")
        if prior is MFOrderState.ACKNOWLEDGED and response.state is MFOrderState.ACKNOWLEDGED:
            raise MFPaperOrderBookError("duplicate acknowledgement transition")
        pending_settlement_id = None
        if (response.state is MFOrderState.ACCEPTED and transaction_type in (
                MFOrderKind.REDEMPTION.value, MFOrderKind.SWITCH_OUT.value)):
            if order_units is None:
                if amount_inr is None:
                    raise MFPaperOrderBookError(
                        "unitless redemption requires a fixed INR amount"
                    )
                # Fixed-INR redemptions are converted to units only at the eligible
                # NAV. The folio-scoped reservation is enforced before value booking.
            else:
                matches = con.execute("""SELECT settlement_id, isin FROM
                    mf_paper_redemption_settlements WHERE account_id=? AND folio_id=?
                      AND scheme_code=? AND plan=? AND option=? AND requested_units=?
                      AND requested_at=? AND state='REDEMPTION_PENDING'
                      AND (order_id IS NULL OR order_id=?)
                    ORDER BY created_at, settlement_id""",
                    [account_id, folio_id, scheme_code, plan, option, order_units,
                     requested_at, response.order_id]).fetchall()
                if len(matches) != 1:
                    raise MFPaperOrderBookError(
                        "unit redemption acceptance requires one matching folio reservation"
                    )
                pending_settlement_id = matches[0][0]
                open_units = con.execute("""SELECT COALESCE(SUM(units_remaining),0)
                    FROM mf_paper_lots WHERE account_id=? AND folio_id=? AND scheme_code=?
                      AND plan=? AND option=? AND isin=? AND units_remaining>0""",
                    [account_id, folio_id, scheme_code, plan, option, matches[0][1]]).fetchone()[0]
                reserved_units = con.execute("""SELECT COALESCE(SUM(requested_units),0)
                    FROM mf_paper_redemption_settlements WHERE account_id=? AND folio_id=?
                      AND scheme_code=? AND plan=? AND option=? AND isin=?
                      AND state='REDEMPTION_PENDING'""",
                    [account_id, folio_id, scheme_code, plan, option,
                     matches[0][1]]).fetchone()[0]
                if reserved_units > open_units:
                    raise MFPaperOrderBookError(
                        "folio redemption reservations exceed currently open units"
                    )
                con.execute("""UPDATE mf_paper_redemption_settlements
                    SET order_id=?, updated_at=? WHERE account_id=? AND settlement_id=?
                      AND (order_id IS NULL OR order_id=?)""",
                    [response.order_id, observed_at, account_id, pending_settlement_id,
                     response.order_id])
        sequence = con.execute("""SELECT COALESCE(MAX(sequence),0)+1
            FROM mf_paper_order_transitions WHERE order_id=?""",
            [response.order_id]).fetchone()[0]
        _append_transition(
            con, order_id=response.order_id, sequence=sequence, state=response.state,
            effective_at=response.effective_at, received_at=response.received_at,
            basis=response.basis.value, source_id=response.source_id,
            source_ref=response.source_ref, source_sha256=response.source_sha256,
            reason=response.reason, evidence=evidence,
        )
        con.execute("UPDATE mf_paper_orders SET current_state=?, updated_at=? WHERE order_id=?",
                    [response.state.value, observed_at, response.order_id])
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return {"order_id": response.order_id, "state": response.state.value,
            "status": "recorded", "is_official_acceptance": response.is_official_acceptance}


def order_snapshot(con, *, order_id: str) -> dict[str, Any]:
    row = con.execute("""SELECT order_id, account_id, folio_id, scheme_code,
        plan, option, transaction_type, requested_at, amount_inr, units,
        request_basis, request_sha256, valid_through, current_state, created_at, updated_at
        FROM mf_paper_orders WHERE order_id=?""", [order_id]).fetchone()
    if row is None:
        raise MFPaperOrderBookError("paper order does not exist")
    keys = ("order_id", "account_id", "folio_id", "scheme_code", "plan", "option",
            "transaction_type", "requested_at", "amount_inr", "units", "request_basis",
            "request_sha256", "valid_through", "current_state", "created_at", "updated_at")
    result = dict(zip(keys, row, strict=True))
    result["transitions"] = con.execute("""SELECT sequence, state, effective_at,
        received_at, basis, source_id, source_ref, source_sha256, reason,
        event_sha256, evidence_json FROM mf_paper_order_transitions
        WHERE order_id=? ORDER BY sequence""", [order_id]).fetchall()
    return result


__all__ = ["MFPaperOrderBookError", "order_snapshot", "record_response", "submit_order"]
