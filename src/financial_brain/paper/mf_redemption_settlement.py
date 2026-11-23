"""Durable MF redemption payout states and receivable release to available cash."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from ..forward_edge.data_permissions import DataPermission, DataUse
from ..kernel.fund_transactions import SchemeTransactionCalendar
from ..kernel.trading_clock import IST
from ..kernel.mf_order import (
    MFOrder, MFOrderAcknowledgement, MFOrderEvidenceBasis, MFOrderKind, MFOrderState,
)
from ..kernel.mf_redemption_eligibility import (
    MFRedemptionEligibilityError, resolve_redemption_eligible_nav,
)
from .mf_lot_book import MfPaperBookError

SETTLEMENT_STATES = (
    "REDEMPTION_PENDING", "VALUE_CONFIRMED", "PAYOUT_PENDING", "CASH_AVAILABLE",
)
PAYOUT_BASES = ("SIMULATED_ASSUMPTION", "OBSERVED_EVIDENCE")
UNIT_QUANTUM = Decimal("0.000000000000000001")


def _aware(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise MfPaperBookError(f"{label} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _units(value: Decimal | str | int) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise MfPaperBookError("redemption units must be decimal") from exc
    if not result.is_finite() or result <= 0:
        raise MfPaperBookError("redemption units must be positive and finite")
    if result.quantize(UNIT_QUANTUM) != result:
        raise MfPaperBookError("redemption units exceed the paper-book precision")
    return result


def _digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _event(
    con, *, account_id: str, settlement_id: str, state: str,
    transitioned_at: datetime, digest: str, evidence: dict[str, Any], sequence: int,
) -> None:
    con.execute("""INSERT INTO mf_paper_redemption_transitions (
        account_id, settlement_id, sequence, state, transitioned_at,
        event_sha256, evidence_json) VALUES (?,?,?,?,?,?,?)""",
        [account_id, settlement_id, sequence, state, transitioned_at, digest,
         json.dumps(evidence, sort_keys=True, separators=(",", ":"), default=str)])


def request_redemption(
    con,
    *,
    account_id: str,
    folio_id: str,
    settlement_id: str,
    scheme_code: str,
    plan: str,
    option: str,
    isin: str,
    requested_units: Decimal | str | int,
    requested_on: date,
    requested_at: datetime,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Reserve MF units for a pending redemption without changing available cash.

    Pending requests count against later request capacity so the same units cannot be
    requested twice while the eligible NAV is not yet known.
    """
    for label, value in (("account_id", account_id), ("folio_id", folio_id),
                         ("settlement_id", settlement_id),
                         ("scheme_code", scheme_code), ("plan", plan),
                         ("option", option), ("isin", isin)):
        if not value.strip():
            raise MfPaperBookError(f"{label} is required")
    units = _units(requested_units)
    request_time = _aware(requested_at, "requested_at")
    event_time = _aware(now or datetime.now(timezone.utc), "event time")
    if event_time < request_time:
        raise MfPaperBookError("request persistence precedes request time")
    if request_time.astimezone(IST).date() != requested_on:
        raise MfPaperBookError("requested_on must match the local request date")
    payload = {
        "account_id": account_id, "settlement_id": settlement_id,
        "folio_id": folio_id,
        "scheme_code": scheme_code, "plan": plan, "option": option,
        "isin": isin, "requested_units": units, "requested_on": requested_on,
        "requested_at": request_time,
    }
    request_hash = _digest(payload)

    con.execute("BEGIN TRANSACTION")
    try:
        old = con.execute("""SELECT request_sha256, state FROM mf_paper_redemption_settlements
            WHERE account_id=? AND settlement_id=?""",
            [account_id, settlement_id]).fetchone()
        if old is not None:
            if old[0] != request_hash:
                raise MfPaperBookError("settlement ID was reused for a different request")
            con.execute("COMMIT")
            return {**payload, "state": old[1], "status": "already_submitted"}

        if con.execute("SELECT 1 FROM mf_paper_accounts WHERE account_id=?",
                       [account_id]).fetchone() is None:
            raise MfPaperBookError("paper account does not exist")
        lot_row = con.execute("""SELECT COALESCE(SUM(units_remaining), 0)
            FROM mf_paper_lots WHERE account_id=? AND scheme_code=? AND plan=?
              AND option=? AND isin=? AND folio_id=? AND units_remaining>0""",
            [account_id, scheme_code, plan, option, isin, folio_id]).fetchone()
        open_units = Decimal(lot_row[0])
        reserved_row = con.execute("""SELECT COALESCE(SUM(requested_units), 0)
            FROM mf_paper_redemption_settlements WHERE account_id=? AND scheme_code=?
              AND plan=? AND option=? AND isin=? AND folio_id=?
              AND state='REDEMPTION_PENDING'""",
            [account_id, scheme_code, plan, option, isin, folio_id]).fetchone()
        reserved_units = Decimal(reserved_row[0])
        if units > open_units - reserved_units:
            unassigned_units = con.execute("""SELECT COALESCE(SUM(units_remaining),0)
                FROM mf_paper_lots WHERE account_id=? AND folio_id IS NULL
                  AND scheme_code=? AND plan=? AND option=? AND isin=?
                  AND units_remaining>0""",
                [account_id, scheme_code, plan, option, isin]).fetchone()[0]
            if Decimal(unassigned_units) > 0:
                raise MfPaperBookError(
                    "legacy lots have unreconciled folio ownership; explicit evidence is "
                    "required before redemption"
                )
            raise MfPaperBookError("redemption request exceeds unreserved open units")

        con.execute("""INSERT INTO mf_paper_redemption_settlements (
            account_id, settlement_id, folio_id, scheme_code, plan, option, isin,
            requested_units, requested_on, requested_at, state, request_sha256,
            created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,'REDEMPTION_PENDING',?,?,?)""",
            [account_id, settlement_id, folio_id, scheme_code, plan, option, isin, units,
             requested_on, request_time, request_hash, event_time, event_time])
        _event(con, account_id=account_id, settlement_id=settlement_id,
               state="REDEMPTION_PENDING", transitioned_at=event_time,
               digest=request_hash, evidence=payload, sequence=1)
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return {**payload, "state": "REDEMPTION_PENDING", "status": "submitted"}


def resolve_redemption_nav_eligibility(
    con,
    *,
    account_id: str,
    settlement_id: str,
    order_id: str,
    scheme_calendar: SchemeTransactionCalendar,
    permissions: tuple[DataPermission, ...] = (),
    decision_at: datetime,
    now: datetime,
) -> dict[str, Any]:
    """Freeze a pending redemption's NAV date from its accepted order and cutoff."""
    decision = _aware(decision_at, "decision_at")
    recorded = _aware(now, "now")
    if recorded < decision:
        raise MfPaperBookError("redemption eligibility persistence precedes decision time")
    if not account_id.strip() or not settlement_id.strip() or not order_id.strip():
        raise MfPaperBookError("account_id, settlement_id and order_id are required")
    settlement = con.execute("""SELECT scheme_code, plan, option, isin, requested_units,
        requested_at, state, eligible_nav_date, order_id, eligibility_sha256, folio_id
        FROM mf_paper_redemption_settlements WHERE account_id=? AND settlement_id=?""",
        [account_id, settlement_id]).fetchone()
    if settlement is None:
        raise MfPaperBookError("redemption settlement request does not exist")
    if settlement[6] != "REDEMPTION_PENDING":
        raise MfPaperBookError("redemption NAV eligibility requires a pending request")

    order_row = con.execute("""SELECT order_id, account_id, folio_id, scheme_code,
        plan, option, transaction_type, requested_at, amount_inr, units,
        request_basis, request_sha256, current_state
        FROM mf_paper_orders WHERE order_id=?""", [order_id]).fetchone()
    if order_row is None:
        raise MfPaperBookError("accepted redemption MF order does not exist")
    if order_row[12] != MFOrderState.ACCEPTED.value:
        raise MfPaperBookError("redemption requires a durable ACCEPTED order")
    order = MFOrder(
        order_id=order_row[0], account_id=order_row[1], folio_id=order_row[2],
        scheme_code=order_row[3], plan=order_row[4], option=order_row[5],
        transaction_type=MFOrderKind(order_row[6]), requested_at=order_row[7],
        amount_inr=order_row[8], units=order_row[9],
        request_basis=MFOrderEvidenceBasis(order_row[10]),
    )
    if (order.account_id != account_id or order.folio_id != settlement[10]
            or order.scheme_code != settlement[0]
            or order.plan != settlement[1] or order.option != settlement[2]
            or order.transaction_type not in (MFOrderKind.REDEMPTION, MFOrderKind.SWITCH_OUT)
            or order.units != Decimal(settlement[4])
            or order.requested_at != settlement[5]):
        raise MfPaperBookError("accepted order does not match the pending redemption request")
    request_receipt = con.execute("""SELECT evidence_json FROM mf_paper_order_transitions
        WHERE order_id=? AND state='REQUESTED' ORDER BY sequence LIMIT 1""", [order_id]).fetchone()
    if request_receipt is None or json.loads(request_receipt[0]).get("request_sha256") != order_row[11]:
        raise MfPaperBookError("persisted redemption request receipt does not reconcile")
    accepted = con.execute("""SELECT effective_at, received_at, basis, source_id,
        source_ref, source_sha256, reason FROM mf_paper_order_transitions
        WHERE order_id=? AND state='ACCEPTED' ORDER BY sequence DESC LIMIT 1""", [order_id]).fetchone()
    if accepted is None:
        raise MfPaperBookError("accepted redemption order is missing its transition receipt")
    acceptance = MFOrderAcknowledgement(
        order_id=order_id, state=MFOrderState.ACCEPTED, effective_at=accepted[0],
        received_at=accepted[1], basis=MFOrderEvidenceBasis(accepted[2]),
        source_id=accepted[3], source_ref=accepted[4], source_sha256=accepted[5],
        reason=accepted[6],
    )
    try:
        result = resolve_redemption_eligible_nav(
            order, acceptance, scheme_calendar, decision_at=decision, permissions=permissions,
        )
    except MFRedemptionEligibilityError as exc:
        raise MfPaperBookError(str(exc)) from exc
    evidence = json.dumps({
        field: getattr(result, field) for field in result.__dataclass_fields__
    }, sort_keys=True, separators=(",", ":"), default=str)

    con.execute("BEGIN TRANSACTION")
    try:
        current = con.execute("""SELECT eligible_nav_date, order_id, eligibility_sha256,
            eligibility_evidence_json FROM mf_paper_redemption_settlements
            WHERE account_id=? AND settlement_id=?""", [account_id, settlement_id]).fetchone()
        if current[2] is not None:
            if (current[0] != result.eligible_nav_date or current[1] != order_id
                    or current[2] != result.event_sha256 or current[3] != evidence):
                raise MfPaperBookError("redemption NAV eligibility is immutable once recorded")
            con.execute("COMMIT")
            return {"account_id": account_id, "settlement_id": settlement_id,
                    "order_id": order_id, "eligible_nav_date": current[0],
                    "eligibility_sha256": current[2], "status": "already_resolved"}
        con.execute("""UPDATE mf_paper_redemption_settlements SET eligible_nav_date=?,
            order_id=?, eligibility_sha256=?, eligibility_evidence_json=?, updated_at=?
            WHERE account_id=? AND settlement_id=? AND state='REDEMPTION_PENDING'""",
            [result.eligible_nav_date, order_id, result.event_sha256, evidence, recorded,
             account_id, settlement_id])
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return {"account_id": account_id, "settlement_id": settlement_id,
            "order_id": order_id, "eligible_nav_date": result.eligible_nav_date,
            "eligibility_sha256": result.event_sha256, "status": "resolved"}


def make_cash_available(
    con,
    *,
    account_id: str,
    settlement_id: str,
    available_at: datetime,
    basis: str = "SIMULATED_ASSUMPTION",
    evidence_ref: str | None = None,
    source_id: str | None = None,
    source_sha256: str | None = None,
    permissions: tuple[DataPermission, ...] = (),
    now: datetime | None = None,
) -> dict[str, Any]:
    """Release confirmed redemption proceeds to spendable account cash exactly once.

    Simulated payouts cannot arrive before their due date. Independently observed
    payouts need a source hash and effective paper-simulation permission, while the
    returned basis label keeps that claim separate from assumptions.
    """
    if not account_id.strip() or not settlement_id.strip():
        raise MfPaperBookError("account_id and settlement_id are required")
    if basis not in PAYOUT_BASES:
        raise MfPaperBookError("basis must be SIMULATED_ASSUMPTION or OBSERVED_EVIDENCE")
    payout_time = _aware(available_at, "available_at")
    event_time = _aware(now or datetime.now(timezone.utc), "event time")
    if event_time < payout_time:
        raise MfPaperBookError("payout cannot be made available before its event time")

    if basis == "OBSERVED_EVIDENCE":
        if not evidence_ref or not source_id or not source_sha256:
            raise MfPaperBookError("observed payout requires source identity, hash and evidence")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", source_sha256):
            raise MfPaperBookError("payout source hash must be a SHA-256 digest")
        payout_date = payout_time.astimezone(IST).date()
        allowed_permissions = [permission for permission in permissions if permission.allows(
            source_id=source_id, use=DataUse.PAPER_SIMULATION, on=payout_date,
        )]
        if not allowed_permissions:
            raise MfPaperBookError("payout source lacks effective PAPER_SIMULATION permission")
        permission_evidence = sorted((
            item.source_id, item.use.value, item.status.value, item.effective_from,
            item.effective_through, item.evidence_ref, item.reviewer,
        ) for item in allowed_permissions)
    elif any((evidence_ref, source_id, source_sha256)):
        raise MfPaperBookError("simulated payout cannot claim external source evidence")
    else:
        permission_evidence = []

    con.execute("BEGIN TRANSACTION")
    try:
        row = con.execute("""SELECT state, net_inr, payout_due_date, payout_sha256
            FROM mf_paper_redemption_settlements
            WHERE account_id=? AND settlement_id=?""",
            [account_id, settlement_id]).fetchone()
        if row is None:
            raise MfPaperBookError("redemption settlement does not exist")
        state, raw_net, due_date, old_hash = row
        payload = {
            "account_id": account_id, "settlement_id": settlement_id,
            "available_at": payout_time, "basis": basis,
            "evidence_ref": evidence_ref, "source_id": source_id,
            "source_sha256": source_sha256.lower() if source_sha256 else None,
            "permission_evidence": permission_evidence,
        }
        payout_hash = _digest(payload)
        if state == "CASH_AVAILABLE":
            if old_hash != payout_hash:
                raise MfPaperBookError("payout was already released with different evidence")
            con.execute("COMMIT")
            return {**payload, "net_inr": Decimal(raw_net), "state": state,
                    "status": "already_applied"}
        if state != "PAYOUT_PENDING":
            raise MfPaperBookError("redemption must be PAYOUT_PENDING before cash release")
        if basis == "SIMULATED_ASSUMPTION" and payout_time.astimezone(IST).date() < due_date:
            raise MfPaperBookError("simulated payout cannot precede its due date")

        net = Decimal(raw_net)
        if con.execute("SELECT 1 FROM mf_paper_accounts WHERE account_id=?",
                       [account_id]).fetchone() is None:
            raise MfPaperBookError("paper account does not exist")
        con.execute("""UPDATE mf_paper_accounts SET cash_inr=cash_inr+?
            WHERE account_id=?""", [net, account_id])
        con.execute("""UPDATE mf_paper_redemption_settlements SET state='CASH_AVAILABLE',
            payout_available_at=?, payout_basis=?, payout_evidence_ref=?, payout_source_id=?,
            payout_source_sha256=?, payout_sha256=?, updated_at=?
            WHERE account_id=? AND settlement_id=?""",
            [payout_time, basis, evidence_ref, source_id,
             source_sha256.lower() if source_sha256 else None, payout_hash, event_time,
             account_id, settlement_id])
        evidence = {**payload, "net_inr": net}
        _event(con, account_id=account_id, settlement_id=settlement_id,
               state="CASH_AVAILABLE", transitioned_at=payout_time,
               digest=payout_hash, evidence=evidence, sequence=4)
        from .mf_account_ledger import post_journal
        post_journal(
            con, account_id=account_id, transaction_id=f"payout:{settlement_id}",
            effective_at=payout_time, recorded_at=event_time,
            entries=(("CASH", net, 0), ("REDEMPTION_RECEIVABLE", 0, net)),
            basis=basis, source_id=source_id, source_ref=evidence_ref,
            source_sha256=source_sha256,
            evidence={"settlement_id": settlement_id, "net_inr": str(net)},
        )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return {**payload, "net_inr": net, "state": "CASH_AVAILABLE", "status": "applied"}


def settlement_snapshot(con, *, account_id: str, settlement_id: str) -> dict[str, Any]:
    row = con.execute("""SELECT account_id, settlement_id, folio_id, scheme_code, plan, option,
        isin, requested_units, requested_on, requested_at, state, redemption_event_id,
        eligible_nav_date, order_id, eligibility_sha256, eligibility_evidence_json,
        nav_observation_id, nav_per_unit, gross_inr, exit_load_inr,
        net_inr, payout_due_date, payout_available_at, payout_basis, payout_evidence_ref,
        payout_source_id, payout_source_sha256, created_at, updated_at
        FROM mf_paper_redemption_settlements WHERE account_id=? AND settlement_id=?""",
        [account_id, settlement_id]).fetchone()
    if row is None:
        raise MfPaperBookError("redemption settlement does not exist")
    keys = (
        "account_id", "settlement_id", "folio_id", "scheme_code", "plan", "option", "isin",
        "requested_units", "requested_on", "requested_at", "state",
        "redemption_event_id", "eligible_nav_date", "order_id", "eligibility_sha256",
        "eligibility_evidence_json", "nav_observation_id", "nav_per_unit",
        "gross_inr", "exit_load_inr", "net_inr", "payout_due_date",
        "payout_available_at", "payout_basis", "payout_evidence_ref", "payout_source_id",
        "payout_source_sha256", "created_at", "updated_at",
    )
    result = dict(zip(keys, row, strict=True))
    result["transitions"] = con.execute("""SELECT state, transitioned_at, event_sha256,
        evidence_json FROM mf_paper_redemption_transitions
        WHERE account_id=? AND settlement_id=? ORDER BY sequence""",
        [account_id, settlement_id]).fetchall()
    return result


__all__ = [
    "make_cash_available", "request_redemption", "resolve_redemption_nav_eligibility",
    "settlement_snapshot",
]
