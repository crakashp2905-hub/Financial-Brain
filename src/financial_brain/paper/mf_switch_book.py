"""Durable MF switch orchestration across redemption, payout and purchase books.

The switch record links independently persisted orders and settlements. It reserves
source units at request, recognizes proceeds only after source cash is available, then
reserves target cash until NAV allotment. Every leg can be replayed after a crash.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from ..forward_edge.data_permissions import DataPermission
from ..kernel.fund_transactions import SchemeTransactionCalendar
from ..kernel.mf_order import MFOrderKind
from ..kernel.mf_switch import MFSwitchRequest
from ..kernel.trading_clock import IST
from ..providers.mf_scheme_terms import SchemeCommercialTerms
from .mf_lot_book import MfPaperBookError


class MFSwitchBookError(ValueError):
    """A durable switch transition cannot be reconciled safely."""


_STATES = (
    "REQUESTED", "SWITCH_OUT_VALUE_CONFIRMED", "SWITCH_OUT_PROCEEDS_AVAILABLE",
    "SWITCH_IN_FUNDS_APPLIED", "SWITCH_IN_ALLOTMENT_CONFIRMED",
)


def _aware(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise MFSwitchBookError(f"{label} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _hash(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _request_payload(request: MFSwitchRequest, settlement_id: str) -> dict[str, Any]:
    return {
        "switch_id": request.switch_id, "account_id": request.account_id,
        "folio_id": request.folio_id,
        "source": [request.source_scheme, request.source_plan, request.source_option,
                   request.source_isin],
        "target": [request.target_scheme, request.target_plan, request.target_option,
                   request.target_isin],
        "requested_units": str(request.requested_units),
        "requested_at": request.requested_at.isoformat(),
        "settlement_id": settlement_id,
    }


def _transition(
    con, *, account_id: str, switch_id: str, state: str, effective_at: datetime,
    amount_inr: Decimal | None, related_id: str | None, evidence: dict[str, Any],
) -> str:
    if state not in _STATES:
        raise MFSwitchBookError("invalid switch state")
    payload = {
        "account_id": account_id, "switch_id": switch_id, "state": state,
        "effective_at": effective_at.isoformat(),
        "amount_inr": str(amount_inr) if amount_inr is not None else None,
        "related_id": related_id, "evidence": evidence,
    }
    digest = _hash(payload)
    existing = con.execute("""SELECT event_sha256 FROM mf_paper_switch_transitions
        WHERE account_id=? AND switch_id=? AND state=?""",
        [account_id, switch_id, state]).fetchone()
    if existing is not None:
        if existing[0] != digest:
            raise MFSwitchBookError("switch transition replay changed its evidence")
        return digest
    previous = con.execute("""SELECT state, effective_at FROM mf_paper_switch_transitions
        WHERE account_id=? AND switch_id=? ORDER BY sequence DESC LIMIT 1""",
        [account_id, switch_id]).fetchone()
    expected_index = 0 if previous is None else _STATES.index(previous[0]) + 1
    if expected_index >= len(_STATES) or _STATES[expected_index] != state:
        raise MFSwitchBookError("switch transition is out of order")
    if previous is not None and effective_at <= previous[1]:
        raise MFSwitchBookError("switch lifecycle stages must occur at strictly later times")
    sequence = expected_index + 1
    con.execute("""INSERT INTO mf_paper_switch_transitions (
        account_id, switch_id, sequence, state, effective_at, amount_inr, related_id,
        event_sha256, evidence_json) VALUES (?,?,?,?,?,?,?,?,?)""",
        [account_id, switch_id, sequence, state, effective_at, amount_inr, related_id,
         digest, json.dumps(evidence, sort_keys=True, separators=(",", ":"), default=str)])
    return digest


def request_switch(
    con, *, request: MFSwitchRequest, settlement_id: str, now: datetime,
) -> dict[str, Any]:
    """Reserve source units and create an idempotent durable paired-switch record."""
    recorded = _aware(now, "now")
    if not settlement_id.strip():
        raise MFSwitchBookError("settlement_id is required")
    if recorded < request.requested_at:
        raise MFSwitchBookError("switch request cannot be persisted before requested_at")
    payload = _request_payload(request, settlement_id)
    digest = _hash(payload)
    from .mf_redemption_settlement import request_redemption

    try:
        request_redemption(
            con, account_id=request.account_id, settlement_id=settlement_id,
            scheme_code=request.source_scheme, plan=request.source_plan,
            option=request.source_option, isin=request.source_isin,
            requested_units=request.requested_units,
            requested_on=request.requested_at.astimezone(IST).date(),
            requested_at=request.requested_at, now=recorded,
        )
    except MfPaperBookError as exc:
        raise MFSwitchBookError(str(exc)) from exc

    con.execute("BEGIN TRANSACTION")
    try:
        old = con.execute("""SELECT request_sha256, state FROM mf_paper_switches
            WHERE account_id=? AND switch_id=?""",
            [request.account_id, request.switch_id]).fetchone()
        if old is not None:
            if old[0] != digest:
                raise MFSwitchBookError("switch_id was reused for different request content")
            con.execute("COMMIT")
            return {"account_id": request.account_id, "switch_id": request.switch_id,
                    "settlement_id": settlement_id, "state": old[1],
                    "status": "already_requested"}
        con.execute("""INSERT INTO mf_paper_switches (
            account_id, switch_id, folio_id, source_scheme, source_plan, source_option,
            source_isin, target_scheme, target_plan, target_option, target_isin,
            requested_units, requested_at, settlement_id, state, request_sha256,
            created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'REQUESTED',?,?,?)""",
            [request.account_id, request.switch_id, request.folio_id,
             request.source_scheme, request.source_plan, request.source_option,
             request.source_isin, request.target_scheme, request.target_plan,
             request.target_option, request.target_isin, request.requested_units,
             request.requested_at, settlement_id, digest, recorded, recorded])
        _transition(
            con, account_id=request.account_id, switch_id=request.switch_id,
            state="REQUESTED", effective_at=request.requested_at,
            amount_inr=request.requested_units, related_id=settlement_id,
            evidence={"request_sha256": digest},
        )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return {"account_id": request.account_id, "switch_id": request.switch_id,
            "settlement_id": settlement_id, "state": "REQUESTED", "status": "requested"}


def link_switch_out_order(con, *, account_id: str, switch_id: str,
                          order_id: str, now: datetime) -> dict[str, Any]:
    """Link the exact persisted SWITCH_OUT order to the unit-reserving settlement."""
    recorded = _aware(now, "now")
    row = con.execute("""SELECT folio_id, source_scheme, source_plan, source_option,
        requested_units, requested_at, settlement_id, source_order_id
        FROM mf_paper_switches WHERE account_id=? AND switch_id=?""",
        [account_id, switch_id]).fetchone()
    if row is None:
        raise MFSwitchBookError("switch request does not exist")
    order = con.execute("""SELECT account_id, folio_id, scheme_code, plan, option,
        transaction_type, requested_at, units, current_state
        FROM mf_paper_orders WHERE order_id=?""", [order_id]).fetchone()
    if order is None:
        raise MFSwitchBookError("switch-out order does not exist")
    if (order[0] != account_id or order[1] != row[0] or order[2:5] != tuple(row[1:4])
            or order[5] != MFOrderKind.SWITCH_OUT.value
            or order[6] != row[5] or Decimal(order[7]) != Decimal(row[4])
            or order[8] != "ACCEPTED"):
        raise MFSwitchBookError("accepted SWITCH_OUT order does not match the switch request")
    if row[7] is not None and row[7] != order_id:
        raise MFSwitchBookError("switch-out order link is immutable")
    accepted_at = con.execute("""SELECT received_at FROM mf_paper_order_transitions
        WHERE order_id=? AND state='ACCEPTED' ORDER BY sequence DESC LIMIT 1""",
        [order_id]).fetchone()
    if accepted_at is None or recorded < accepted_at[0]:
        raise MFSwitchBookError("switch-out order link predates its accepted receipt")
    settlement_order = con.execute("""SELECT order_id FROM mf_paper_redemption_settlements
        WHERE account_id=? AND settlement_id=?""", [account_id, row[6]]).fetchone()
    if settlement_order is None or settlement_order[0] not in (None, order_id):
        raise MFSwitchBookError("redemption settlement is already linked to another order")
    con.execute("BEGIN TRANSACTION")
    try:
        con.execute("""UPDATE mf_paper_switches SET source_order_id=?, updated_at=?
            WHERE account_id=? AND switch_id=? AND (source_order_id IS NULL OR source_order_id=?)""",
            [order_id, recorded, account_id, switch_id, order_id])
        con.execute("""UPDATE mf_paper_redemption_settlements SET order_id=?, updated_at=?
            WHERE account_id=? AND settlement_id=? AND (order_id IS NULL OR order_id=?)""",
            [order_id, recorded, account_id, row[6], order_id])
        linked = con.execute("""SELECT s.source_order_id, r.order_id
            FROM mf_paper_switches s JOIN mf_paper_redemption_settlements r
              ON r.account_id=s.account_id AND r.settlement_id=s.settlement_id
            WHERE s.account_id=? AND s.switch_id=?""", [account_id, switch_id]).fetchone()
        if linked != (order_id, order_id):
            raise MFSwitchBookError("switch-out order and redemption settlement did not link atomically")
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return {"account_id": account_id, "switch_id": switch_id,
            "order_id": order_id, "status": "linked"}


def resolve_switch_out_nav(
    con, *, account_id: str, switch_id: str, scheme_calendar: SchemeTransactionCalendar,
    permissions: tuple[DataPermission, ...] = (), decision_at: datetime, now: datetime,
) -> dict[str, Any]:
    """Freeze the source-leg NAV date from its accepted SWITCH_OUT order and cutoff."""
    row = con.execute("""SELECT source_order_id, settlement_id FROM mf_paper_switches
        WHERE account_id=? AND switch_id=?""", [account_id, switch_id]).fetchone()
    if row is None or row[0] is None:
        raise MFSwitchBookError("switch-out order must be linked before NAV resolution")
    from .mf_redemption_settlement import resolve_redemption_nav_eligibility

    try:
        result = resolve_redemption_nav_eligibility(
            con, account_id=account_id, settlement_id=row[1], order_id=row[0],
            scheme_calendar=scheme_calendar, permissions=permissions,
            decision_at=decision_at, now=now,
        )
    except MfPaperBookError as exc:
        raise MFSwitchBookError(str(exc)) from exc
    return result


def confirm_switch_out_value(con, *, account_id: str, switch_id: str,
                             now: datetime) -> dict[str, Any]:
    """Record source value only after the durable redemption settlement is valued."""
    recorded = _aware(now, "now")
    switch = con.execute("""SELECT settlement_id, source_order_id, state
        FROM mf_paper_switches WHERE account_id=? AND switch_id=?""",
        [account_id, switch_id]).fetchone()
    if switch is None or switch[1] is None:
        raise MFSwitchBookError("switch-out order is not linked")
    settlement = con.execute("""SELECT state, order_id, net_inr, value_sha256,
        redemption_event_id FROM mf_paper_redemption_settlements
        WHERE account_id=? AND settlement_id=?""",
        [account_id, switch[0]]).fetchone()
    if settlement is None or settlement[0] not in {"VALUE_CONFIRMED", "PAYOUT_PENDING", "CASH_AVAILABLE"}:
        raise MFSwitchBookError("switch-out settlement has no confirmed value")
    if settlement[1] != switch[1] or settlement[4] is None or settlement[2] is None:
        raise MFSwitchBookError("switch-out settlement does not reconcile to its accepted order")
    value_event = con.execute("""SELECT created_at FROM mf_paper_events
        WHERE account_id=? AND event_id=?""", [account_id, settlement[4]]).fetchone()
    if value_event is None:
        raise MFSwitchBookError("switch-out redemption event is missing")
    effective = _aware(value_event[0], "redemption value time")
    if recorded < effective:
        raise MFSwitchBookError("switch-out value cannot be recorded before redemption")
    amount = Decimal(settlement[2])
    evidence = {"settlement_id": switch[0], "order_id": switch[1],
                "value_sha256": settlement[3], "redemption_event_id": settlement[4]}
    con.execute("BEGIN TRANSACTION")
    try:
        _transition(con, account_id=account_id, switch_id=switch_id,
                    state="SWITCH_OUT_VALUE_CONFIRMED", effective_at=effective,
                    amount_inr=amount, related_id=settlement[4], evidence=evidence)
        con.execute("""UPDATE mf_paper_switches SET state='SWITCH_OUT_VALUE_CONFIRMED',
            source_net_inr=?, updated_at=? WHERE account_id=? AND switch_id=?
            AND state='REQUESTED'""", [amount, recorded, account_id, switch_id])
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return {"account_id": account_id, "switch_id": switch_id,
            "state": "SWITCH_OUT_VALUE_CONFIRMED", "net_inr": amount,
            "status": "confirmed"}


def confirm_switch_out_proceeds(con, *, account_id: str, switch_id: str,
                               now: datetime) -> dict[str, Any]:
    """Make switch proceeds usable only after payout is recorded in canonical cash."""
    recorded = _aware(now, "now")
    row = con.execute("""SELECT settlement_id, state, source_net_inr
        FROM mf_paper_switches WHERE account_id=? AND switch_id=?""",
        [account_id, switch_id]).fetchone()
    if row is None:
        raise MFSwitchBookError("switch request does not exist")
    settlement = con.execute("""SELECT state, net_inr, payout_available_at, payout_sha256
        FROM mf_paper_redemption_settlements WHERE account_id=? AND settlement_id=?""",
        [account_id, row[0]]).fetchone()
    if settlement is None or settlement[0] != "CASH_AVAILABLE":
        raise MFSwitchBookError("switch-out proceeds are not in available account cash")
    if row[2] is None or Decimal(settlement[1]) != Decimal(row[2]):
        raise MFSwitchBookError("available switch proceeds differ from the confirmed source value")
    effective = _aware(settlement[2], "payout_available_at")
    value = con.execute("""SELECT effective_at FROM mf_paper_switch_transitions
        WHERE account_id=? AND switch_id=? AND state='SWITCH_OUT_VALUE_CONFIRMED'""",
        [account_id, switch_id]).fetchone()
    if value is None or effective <= value[0]:
        raise MFSwitchBookError("switch proceeds must settle strictly after source value confirmation")
    if recorded < effective:
        raise MFSwitchBookError("switch proceeds cannot be recorded before cash availability")
    evidence = {"settlement_id": row[0], "payout_sha256": settlement[3],
                "available_at": effective.isoformat(), "net_inr": str(settlement[1])}
    amount = Decimal(settlement[1])
    con.execute("BEGIN TRANSACTION")
    try:
        _transition(con, account_id=account_id, switch_id=switch_id,
                    state="SWITCH_OUT_PROCEEDS_AVAILABLE", effective_at=effective,
                    amount_inr=amount, related_id=row[0], evidence=evidence)
        con.execute("""UPDATE mf_paper_switches SET state='SWITCH_OUT_PROCEEDS_AVAILABLE',
            proceeds_available_at=?, updated_at=? WHERE account_id=? AND switch_id=?
            AND state='SWITCH_OUT_VALUE_CONFIRMED'""",
            [effective, recorded, account_id, switch_id])
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return {"account_id": account_id, "switch_id": switch_id,
            "state": "SWITCH_OUT_PROCEEDS_AVAILABLE", "available_at": effective,
            "net_inr": amount, "status": "available"}


def link_switch_in_order(con, *, account_id: str, switch_id: str,
                         order_id: str, now: datetime) -> dict[str, Any]:
    """Link an accepted SWITCH_IN order requested strictly after source payout."""
    recorded = _aware(now, "now")
    switch = con.execute("""SELECT target_scheme, target_plan, target_option,
        target_order_id, state, proceeds_available_at, source_net_inr
        FROM mf_paper_switches WHERE account_id=? AND switch_id=?""",
        [account_id, switch_id]).fetchone()
    if switch is None or switch[4] not in _STATES[2:]:
        raise MFSwitchBookError("switch proceeds must be available before switch-in order")
    order = con.execute("""SELECT account_id, scheme_code, plan, option,
        transaction_type, requested_at, amount_inr, current_state
        FROM mf_paper_orders WHERE order_id=?""", [order_id]).fetchone()
    if order is None:
        raise MFSwitchBookError("switch-in order does not exist")
    if (order[0] != account_id or order[1:4] != tuple(switch[:3])
            or order[4] != MFOrderKind.SWITCH_IN.value or order[7] != "ACCEPTED"):
        raise MFSwitchBookError("accepted SWITCH_IN order does not match the target scheme")
    if order[5] <= switch[5]:
        raise MFSwitchBookError("switch-in order must be requested after source proceeds are available")
    accepted_at = con.execute("""SELECT received_at FROM mf_paper_order_transitions
        WHERE order_id=? AND state='ACCEPTED' ORDER BY sequence DESC LIMIT 1""",
        [order_id]).fetchone()
    if accepted_at is None or recorded < accepted_at[0]:
        raise MFSwitchBookError("switch-in order link predates its accepted receipt")
    if order[6] is None or Decimal(order[6]) > Decimal(switch[6]):
        raise MFSwitchBookError("switch-in amount exceeds available switch proceeds")
    if switch[3] is not None and switch[3] != order_id:
        raise MFSwitchBookError("switch-in order link is immutable")
    con.execute("""UPDATE mf_paper_switches SET target_order_id=?, updated_at=?
        WHERE account_id=? AND switch_id=?""", [order_id, recorded, account_id, switch_id])
    return {"account_id": account_id, "switch_id": switch_id,
            "order_id": order_id, "status": "linked"}


def apply_switch_in_funds(
    con, *, account_id: str, switch_id: str, initiated_at: datetime,
    credited_at: datetime, available_at: datetime, recorded_at: datetime,
) -> dict[str, Any]:
    """Record simulated switch-in payment clocks and reserve cash until allotment."""
    initiated = _aware(initiated_at, "initiated_at")
    credited = _aware(credited_at, "credited_at")
    available = _aware(available_at, "available_at")
    recorded = _aware(recorded_at, "recorded_at")
    row = con.execute("""SELECT target_order_id, state, proceeds_available_at,
        source_net_inr FROM mf_paper_switches WHERE account_id=? AND switch_id=?""",
        [account_id, switch_id]).fetchone()
    if row is None or row[0] is None:
        raise MFSwitchBookError("switch-in order must be linked first")
    if row[1] not in {
        "SWITCH_OUT_PROCEEDS_AVAILABLE", "SWITCH_IN_FUNDS_APPLIED",
        "SWITCH_IN_ALLOTMENT_CONFIRMED",
    }:
        raise MFSwitchBookError("switch proceeds must be available before target funding")
    if not (initiated > row[2] and credited >= initiated and available >= credited):
        raise MFSwitchBookError("switch-in funding clocks must follow source payout in order")
    if recorded < available:
        raise MFSwitchBookError("switch-in funds cannot be recorded before utilization availability")
    order = con.execute("""SELECT amount_inr, requested_at, current_state
        FROM mf_paper_orders WHERE order_id=? AND account_id=?""",
        [row[0], account_id]).fetchone()
    if order is None or order[2] != "ACCEPTED" or order[0] is None:
        raise MFSwitchBookError("switch-in order must be durably accepted")
    amount = Decimal(order[0])
    if amount > Decimal(row[3]) or initiated < order[1]:
        raise MFSwitchBookError("switch-in payment exceeds proceeds or precedes its order")
    from ..kernel.mf_purchase_payment import (
        MFPurchasePaymentMilestone, PaymentEvidenceBasis, PaymentMilestoneKind,
    )
    from .mf_purchase_book import record_payment_milestone

    for kind, effective_at in (
        (PaymentMilestoneKind.INITIATED, initiated),
        (PaymentMilestoneKind.CREDITED_TO_SCHEME, credited),
        (PaymentMilestoneKind.AVAILABLE_FOR_UTILIZATION, available),
    ):
        record_payment_milestone(
            con, order_id=row[0], milestone=MFPurchasePaymentMilestone(
                kind=kind, effective_at=effective_at, received_at=effective_at,
                basis=PaymentEvidenceBasis.SIMULATED_ASSUMPTION,
            ), now=recorded,
        )
    evidence = {"target_order_id": row[0], "payment_basis": "SIMULATED_ASSUMPTION",
                "initiated_at": initiated.isoformat(), "credited_at": credited.isoformat(),
                "available_at": available.isoformat(), "amount_inr": str(amount)}
    con.execute("BEGIN TRANSACTION")
    try:
        current = con.execute("""SELECT state FROM mf_paper_switches
            WHERE account_id=? AND switch_id=?""", [account_id, switch_id]).fetchone()
        if current is None:
            raise MFSwitchBookError("switch request does not exist")
        if current[0] in {"SWITCH_IN_FUNDS_APPLIED", "SWITCH_IN_ALLOTMENT_CONFIRMED"}:
            _transition(con, account_id=account_id, switch_id=switch_id,
                        state="SWITCH_IN_FUNDS_APPLIED", effective_at=available,
                        amount_inr=amount, related_id=row[0], evidence=evidence)
            con.execute("COMMIT")
            return {"account_id": account_id, "switch_id": switch_id,
                    "state": current[0], "amount_inr": amount,
                    "status": "already_applied"}
        account = con.execute("SELECT cash_inr FROM mf_paper_accounts WHERE account_id=?",
                              [account_id]).fetchone()
        if account is None or Decimal(account[0]) < amount:
            raise MFSwitchBookError("available paper cash is insufficient for switch-in funds")
        from .mf_account_ledger import post_journal
        con.execute("UPDATE mf_paper_accounts SET cash_inr=cash_inr-? WHERE account_id=?",
                    [amount, account_id])
        post_journal(
            con, account_id=account_id, transaction_id=f"switch-in-funds:{switch_id}",
            effective_at=available, recorded_at=recorded,
            entries=(("SIP_PAYMENT_PENDING", amount, 0), ("CASH", 0, amount)),
            evidence={**evidence, "switch_id": switch_id},
        )
        _transition(con, account_id=account_id, switch_id=switch_id,
                    state="SWITCH_IN_FUNDS_APPLIED", effective_at=available,
                    amount_inr=amount, related_id=row[0], evidence=evidence)
        con.execute("""UPDATE mf_paper_switches SET state='SWITCH_IN_FUNDS_APPLIED',
            funds_applied_inr=?, updated_at=? WHERE account_id=? AND switch_id=?
            AND state='SWITCH_OUT_PROCEEDS_AVAILABLE'""",
            [amount, recorded, account_id, switch_id])
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return {"account_id": account_id, "switch_id": switch_id,
            "state": "SWITCH_IN_FUNDS_APPLIED", "amount_inr": amount,
            "status": "applied"}


def apply_switch_in_allotment(
    con, *, account_id: str, switch_id: str, nav_observation_id: str,
    nav_per_unit: Decimal | str | int, terms: SchemeCommercialTerms,
    permissions: tuple[DataPermission, ...], decision_at: datetime,
    allotted_at: datetime, now: datetime, lock_in_until=None,
) -> dict[str, Any]:
    """Allot the target units through the normal purchase book from reserved switch cash."""
    row = con.execute("""SELECT target_order_id, state, funds_applied_inr
        FROM mf_paper_switches WHERE account_id=? AND switch_id=?""",
        [account_id, switch_id]).fetchone()
    if row is None or row[0] is None or row[1] not in {
        "SWITCH_IN_FUNDS_APPLIED", "SWITCH_IN_ALLOTMENT_CONFIRMED",
    }:
        raise MFSwitchBookError("switch-in funds must be applied before target allotment")
    from .mf_purchase_book import apply_allotment

    try:
        allotment = apply_allotment(
            con, order_id=row[0], nav_observation_id=nav_observation_id,
            nav_per_unit=nav_per_unit, terms=terms, permissions=permissions,
            decision_at=decision_at, allotted_at=allotted_at, now=now,
            lock_in_until=lock_in_until,
        )
    except (MfPaperBookError, ValueError) as exc:
        raise MFSwitchBookError(str(exc)) from exc
    if allotment["state"] != "ALLOTTED":
        raise MFSwitchBookError("target purchase did not reach ALLOTTED state")
    amount = Decimal(row[2])
    units = Decimal(allotment["units"])
    nav = Decimal(str(nav_per_unit))
    effective = _aware(allotted_at, "allotted_at")
    evidence = {"target_order_id": row[0], "units": str(units),
                "nav_per_unit": str(nav), "nav_observation_id": nav_observation_id,
                "purchase_state": "ALLOTTED"}
    con.execute("BEGIN TRANSACTION")
    try:
        state = con.execute("""SELECT state FROM mf_paper_switches
            WHERE account_id=? AND switch_id=?""", [account_id, switch_id]).fetchone()
        if state[0] == "SWITCH_IN_ALLOTMENT_CONFIRMED":
            con.execute("COMMIT")
            return {"account_id": account_id, "switch_id": switch_id,
                    **allotment, "status": "already_allotted"}
        _transition(con, account_id=account_id, switch_id=switch_id,
                    state="SWITCH_IN_ALLOTMENT_CONFIRMED", effective_at=effective,
                    amount_inr=amount, related_id=row[0], evidence=evidence)
        con.execute("""UPDATE mf_paper_switches SET
            state='SWITCH_IN_ALLOTMENT_CONFIRMED', target_units=?, target_nav=?,
            updated_at=? WHERE account_id=? AND switch_id=?
            AND state='SWITCH_IN_FUNDS_APPLIED'""",
            [units, nav, _aware(now, "now"), account_id, switch_id])
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return {"account_id": account_id, "switch_id": switch_id,
            **allotment, "status": "allotted"}


def switch_snapshot(con, *, account_id: str, switch_id: str) -> dict[str, Any]:
    row = con.execute("""SELECT account_id, switch_id, folio_id,
        source_scheme, source_plan, source_option, source_isin,
        target_scheme, target_plan, target_option, target_isin,
        requested_units, requested_at, settlement_id, source_order_id, target_order_id,
        state, source_net_inr, proceeds_available_at, funds_applied_inr,
        target_units, target_nav, request_sha256, created_at, updated_at
        FROM mf_paper_switches WHERE account_id=? AND switch_id=?""",
        [account_id, switch_id]).fetchone()
    if row is None:
        raise MFSwitchBookError("switch request does not exist")
    keys = ("account_id", "switch_id", "folio_id", "source_scheme", "source_plan",
            "source_option", "source_isin", "target_scheme", "target_plan",
            "target_option", "target_isin", "requested_units", "requested_at",
            "settlement_id", "source_order_id", "target_order_id", "state",
            "source_net_inr", "proceeds_available_at", "funds_applied_inr",
            "target_units", "target_nav", "request_sha256", "created_at", "updated_at")
    result = dict(zip(keys, row, strict=True))
    result["transitions"] = con.execute("""SELECT sequence, state, effective_at,
        amount_inr, related_id, event_sha256, evidence_json
        FROM mf_paper_switch_transitions WHERE account_id=? AND switch_id=? ORDER BY sequence""",
        [account_id, switch_id]).fetchall()
    result["source_settlement"] = con.execute(
        "SELECT state FROM mf_paper_redemption_settlements WHERE account_id=? AND settlement_id=?",
        [account_id, result["settlement_id"]],
    ).fetchone()
    return result


__all__ = [
    "MFSwitchBookError", "apply_switch_in_allotment", "apply_switch_in_funds",
    "confirm_switch_out_proceeds", "confirm_switch_out_value", "link_switch_in_order",
    "link_switch_out_order", "request_switch", "resolve_switch_out_nav", "switch_snapshot",
]
