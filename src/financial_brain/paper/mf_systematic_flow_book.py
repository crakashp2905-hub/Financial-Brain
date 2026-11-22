"""Durable SWP/STP paper instructions and append-only settlement events."""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_DOWN
from typing import Any

from ..forward_edge.data_permissions import DataPermission, DataUse
from ..providers.mf_scheme_terms import SchemeCommercialTerms
from ..kernel.mf_systematic_flows import (
    MFSystematicFlowBasis, MFSystematicFlowError, MFSystematicFlowEvent,
    MFSystematicFlowEventKind, MFSystematicFlowInstruction,
    MFSystematicFlowKind, MFSystematicFlowLifecycle,
)
from ..kernel.trading_clock import IST
from ..kernel.fund_transactions import SchemeTransactionCalendar
from ..kernel.mf_order import (
    MFOrder, MFOrderAcknowledgement, MFOrderEvidenceBasis, MFOrderKind, MFOrderState,
)


class MFSystematicFlowBookError(ValueError):
    """Invalid or unauthorized durable SWP/STP paper operation."""


def _aware(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise MFSystematicFlowBookError(f"{label} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _decimal_text(value: Decimal | None) -> str | None:
    return format(value.normalize(), "f") if value is not None else None


def _instruction_payload(item: MFSystematicFlowInstruction) -> dict[str, Any]:
    return {
        "instruction_id": item.instruction_id, "plan_id": item.plan_id,
        "account_id": item.account_id, "folio_id": item.folio_id,
        "kind": item.kind.value, "scheduled_for": item.scheduled_for.isoformat(),
        "source_scheme": item.source_scheme, "source_plan": item.source_plan,
        "source_option": item.source_option, "source_isin": item.source_isin,
        "amount_inr": _decimal_text(item.amount_inr),
        "units": _decimal_text(item.units),
        "target_scheme": item.target_scheme, "target_plan": item.target_plan,
        "target_option": item.target_option, "target_isin": item.target_isin,
    }


def _event_payload(event: MFSystematicFlowEvent) -> dict[str, Any]:
    return {
        "event_id": event.event_id, "kind": event.kind.value,
        "effective_at": event.effective_at.isoformat(), "basis": event.basis.value,
        "amount_inr": _decimal_text(event.amount_inr),
        "gross_inr": _decimal_text(event.gross_inr),
        "exit_load_inr": _decimal_text(event.exit_load_inr),
        "other_charges_inr": _decimal_text(event.other_charges_inr),
        "nav_per_unit": _decimal_text(event.nav_per_unit),
        "units": _decimal_text(event.units),
        "reason": event.reason, "source_id": event.source_id,
        "source_ref": event.source_ref, "source_sha256": event.source_sha256,
    }


_I_COLUMNS = """instruction_id, plan_id, account_id, folio_id, kind, scheduled_for,
    source_scheme, source_plan, source_option, source_isin, amount_inr, units,
    target_scheme, target_plan, target_option, target_isin, current_stage,
    state_sha256, created_at, updated_at"""


def _instruction_from_row(row) -> MFSystematicFlowInstruction:
    return MFSystematicFlowInstruction(
        instruction_id=row[0], plan_id=row[1], account_id=row[2], folio_id=row[3],
        kind=MFSystematicFlowKind(row[4]), scheduled_for=row[5], source_scheme=row[6],
        source_plan=row[7], source_option=row[8], source_isin=row[9], amount_inr=row[10],
        units=row[11], target_scheme=row[12], target_plan=row[13],
        target_option=row[14], target_isin=row[15],
    )


def _event_from_row(row) -> MFSystematicFlowEvent:
    return MFSystematicFlowEvent(
        event_id=row[0], kind=MFSystematicFlowEventKind(row[1]), effective_at=row[2],
        basis=MFSystematicFlowBasis(row[3]), amount_inr=row[4], gross_inr=row[5],
        exit_load_inr=row[6], other_charges_inr=row[7], nav_per_unit=row[8],
        units=row[9], reason=row[10], source_id=row[11], source_ref=row[12],
        source_sha256=row[13],
    )


def _record_event_once(
    con, *, instruction_id: str, event: MFSystematicFlowEvent, now: datetime,
    permissions: tuple[DataPermission, ...],
) -> dict[str, object]:
    """Idempotently append evidence even when replaying an earlier completed stage."""
    existing = con.execute("""SELECT event_id, kind, effective_at, basis, amount_inr,
        gross_inr, exit_load_inr, other_charges_inr, nav_per_unit, units, reason,
        source_id, source_ref, source_sha256 FROM mf_paper_systematic_events
        WHERE instruction_id=? AND event_id=?""",
        [instruction_id, event.event_id]).fetchone()
    if existing is not None:
        existing_payload = _event_payload(_event_from_row(existing))
        requested_payload = _event_payload(event)
        if existing_payload != requested_payload:
            differences = {key: (existing_payload[key], requested_payload[key])
                           for key in requested_payload
                           if existing_payload[key] != requested_payload[key]}
            raise MFSystematicFlowBookError(
                f"systematic event ID was already used for different evidence: {differences}"
            )
        snapshot = systematic_instruction_snapshot(con, instruction_id=instruction_id)
        return {"instruction_id": instruction_id, "stage": snapshot["stage"],
                "status": "already_recorded"}
    return record_systematic_event(
        con, instruction_id=instruction_id, event=event, now=now,
        permissions=permissions,
    )


def create_systematic_plan(con, *, plan_id: str,
                           instructions: tuple[MFSystematicFlowInstruction, ...],
                           now: datetime) -> dict[str, object]:
    """Persist a frozen set of period instructions idempotently."""
    recorded_at = _aware(now, "now")
    if not plan_id.strip() or not instructions:
        raise MFSystematicFlowBookError("plan_id and at least one instruction are required")
    if any(item.plan_id != plan_id for item in instructions):
        raise MFSystematicFlowBookError("all instructions must belong to the requested plan")
    if len({item.instruction_id for item in instructions}) != len(instructions):
        raise MFSystematicFlowBookError("instruction IDs must be unique")
    if len({item.scheduled_for for item in instructions}) != len(instructions):
        raise MFSystematicFlowBookError("plan cannot contain duplicate scheduled dates")
    account_ids = {item.account_id for item in instructions}
    if len(account_ids) != 1:
        raise MFSystematicFlowBookError("one periodic plan must use one paper account")
    plan_hash = _hash([_instruction_payload(item) for item in sorted(
        instructions, key=lambda item: item.scheduled_for)])
    con.execute("BEGIN TRANSACTION")
    try:
        account_id = next(iter(account_ids))
        if con.execute("SELECT 1 FROM mf_paper_accounts WHERE account_id=?",
                       [account_id]).fetchone() is None:
            raise MFSystematicFlowBookError("paper account does not exist")
        existing = con.execute("SELECT plan_sha256 FROM mf_paper_systematic_plans WHERE plan_id=?",
                               [plan_id]).fetchone()
        if existing is not None:
            if existing[0] != plan_hash:
                raise MFSystematicFlowBookError("systematic plan ID reused for a different schedule")
            con.execute("COMMIT")
            return {"plan_id": plan_id, "status": "already_created"}
        con.execute("""INSERT INTO mf_paper_systematic_plans
            (plan_id, account_id, plan_sha256, created_at) VALUES (?,?,?,?)""",
            [plan_id, account_id, plan_hash, recorded_at])
        for item in instructions:
            payload = _instruction_payload(item)
            state_hash = _hash({"instruction": payload, "events": []})
            values = [payload[key] for key in (
                "instruction_id", "plan_id", "account_id", "folio_id", "kind",
                "scheduled_for", "source_scheme", "source_plan", "source_option",
                "source_isin", "amount_inr", "units", "target_scheme", "target_plan",
                "target_option", "target_isin")]
            con.execute(f"""INSERT INTO mf_paper_systematic_instructions ({_I_COLUMNS})
                VALUES ({','.join('?' for _ in range(20))})""",
                [*values, "SCHEDULED", state_hash, recorded_at, recorded_at])
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return {"plan_id": plan_id, "status": "created",
            "instruction_count": len(instructions)}


def record_systematic_event(
    con,
    *,
    instruction_id: str,
    event: MFSystematicFlowEvent,
    now: datetime,
    permissions: tuple[DataPermission, ...] = (),
) -> dict[str, object]:
    """Append an ordered event; observed data requires effective paper-use permission."""
    recorded_at = _aware(now, "now")
    if recorded_at < event.effective_at:
        raise MFSystematicFlowBookError("event recorded before effective time")
    allowed: list[dict[str, object]] = []
    if event.basis is MFSystematicFlowBasis.OBSERVED_EVIDENCE:
        when = event.effective_at.astimezone(IST).date()
        matches = [permission for permission in permissions if permission.allows(
            source_id=event.source_id or "", use=DataUse.PAPER_SIMULATION, on=when,
        )]
        if not matches:
            raise MFSystematicFlowBookError("observed event lacks PAPER_SIMULATION permission")
        allowed = []
        for permission in matches:
            effective_from = permission.effective_from
            if effective_from is None:
                raise MFSystematicFlowBookError("effective permission has no start date")
            effective_through = permission.effective_through
            allowed.append({"source_id": permission.source_id,
                "evidence_ref": permission.evidence_ref, "reviewer": permission.reviewer,
                "effective_from": effective_from.isoformat(),
                "effective_through": (effective_through.isoformat()
                                      if effective_through is not None else None)})
    con.execute("BEGIN TRANSACTION")
    try:
        row = con.execute(f"""SELECT {_I_COLUMNS} FROM mf_paper_systematic_instructions
            WHERE instruction_id=?""", [instruction_id]).fetchone()
        if row is None:
            raise MFSystematicFlowBookError("systematic instruction does not exist")
        instruction = _instruction_from_row(row)
        old_events = con.execute("""SELECT event_id, kind, effective_at, basis, amount_inr,
            gross_inr, exit_load_inr, other_charges_inr, nav_per_unit, units, reason,
            source_id, source_ref, source_sha256 FROM mf_paper_systematic_events
            WHERE instruction_id=? ORDER BY sequence""", [instruction_id]).fetchall()
        events = tuple(_event_from_row(values) for values in old_events)
        lifecycle = MFSystematicFlowLifecycle(instruction, events)
        evidence = {"event": _event_payload(event), "permission_evidence": allowed}
        digest = _hash({"instruction_id": instruction_id, "event": _event_payload(event),
                        "permission_evidence": allowed})
        if events and events[-1].event_id == event.event_id:
            last_hash = con.execute("""SELECT event_sha256 FROM mf_paper_systematic_events
                WHERE instruction_id=? AND event_id=?""", [instruction_id, event.event_id]).fetchone()
            if last_hash is not None and last_hash[0] == digest:
                con.execute("COMMIT")
                return {"instruction_id": instruction_id, "stage": lifecycle.stage,
                        "status": "already_recorded"}
            raise MFSystematicFlowBookError("systematic event ID reused with different evidence")
        try:
            next_state = lifecycle.append(event)
        except MFSystematicFlowError as exc:
            raise MFSystematicFlowBookError(str(exc)) from exc
        sequence = len(events) + 1
        con.execute("""INSERT INTO mf_paper_systematic_events (
            instruction_id, sequence, event_id, kind, effective_at, recorded_at,
            basis, amount_inr, gross_inr, exit_load_inr, other_charges_inr,
            nav_per_unit, units, reason, source_id, source_ref, source_sha256,
            event_sha256, evidence_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [instruction_id, sequence, event.event_id, event.kind.value,
             event.effective_at, recorded_at, event.basis.value, event.amount_inr,
             event.gross_inr, event.exit_load_inr, event.other_charges_inr,
             event.nav_per_unit, event.units, event.reason, event.source_id,
             event.source_ref, event.source_sha256, digest,
             json.dumps(evidence, sort_keys=True, separators=(",", ":"))])
        state_hash = _hash({"instruction": _instruction_payload(instruction),
                            "events": [_event_payload(item) for item in (*events, event)]})
        con.execute("""UPDATE mf_paper_systematic_instructions
            SET current_stage=?, state_sha256=?, updated_at=? WHERE instruction_id=?""",
            [next_state.stage, state_hash, recorded_at, instruction_id])
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return {"instruction_id": instruction_id, "stage": next_state.stage, "status": "recorded"}


def request_systematic_redemption(
    con,
    *,
    instruction_id: str,
    requested_at: datetime,
    now: datetime,
    scheme_calendar: SchemeTransactionCalendar,
    permissions: tuple[DataPermission, ...] = (),
) -> dict[str, object]:
    """Create a durable recurring redemption request for units or fixed INR.

    Unit requests reserve units immediately. Fixed-INR requests persist the
    accepted order and cutoff/NAV eligibility first, then reserve the precise
    fractional units once the eligible as-known NAV is available.
    """
    requested_time = _aware(requested_at, "requested_at")
    recorded_at = _aware(now, "now")
    if recorded_at < requested_time:
        raise MFSystematicFlowBookError("request persistence precedes request time")
    row = con.execute(f"""SELECT {_I_COLUMNS} FROM mf_paper_systematic_instructions
        WHERE instruction_id=?""", [instruction_id]).fetchone()
    if row is None:
        raise MFSystematicFlowBookError("systematic instruction does not exist")
    instruction = _instruction_from_row(row)
    from .mf_order_book import record_response, submit_order
    from .mf_redemption_settlement import request_redemption, resolve_redemption_nav_eligibility

    settlement_id = f"systematic:{instruction.instruction_id}"
    if instruction.units is not None:
        settlement = request_redemption(
            con, account_id=instruction.account_id, folio_id=instruction.folio_id,
            settlement_id=settlement_id,
            scheme_code=instruction.source_scheme, plan=instruction.source_plan,
            option=instruction.source_option, isin=instruction.source_isin,
            requested_units=instruction.units,
            requested_on=requested_time.astimezone(IST).date(),
            requested_at=requested_time, now=recorded_at,
        )
    else:
        settlement = {"settlement_id": settlement_id,
                      "state": "AWAITING_ELIGIBLE_NAV", "status": "awaiting_nav"}
    order_id = f"systematic-order:{instruction.instruction_id}"
    order = MFOrder(
        order_id=order_id, account_id=instruction.account_id, folio_id=instruction.folio_id,
        scheme_code=instruction.source_scheme, plan=instruction.source_plan,
        option=instruction.source_option, transaction_type=MFOrderKind.REDEMPTION,
        requested_at=requested_time, amount_inr=instruction.amount_inr,
        units=instruction.units,
    )
    submit_order(
        con, order, valid_business_days=1, calendar=scheme_calendar, now=recorded_at,
    )
    record_response(
        con, MFOrderAcknowledgement(
            order_id=order_id, state=MFOrderState.ACCEPTED,
            effective_at=requested_time, received_at=requested_time,
            basis=MFOrderEvidenceBasis.SIMULATED_ASSUMPTION,
        ), permissions=permissions, now=recorded_at,
    )
    if instruction.units is not None:
        resolve_redemption_nav_eligibility(
            con, account_id=instruction.account_id, settlement_id=settlement_id,
            order_id=order_id, scheme_calendar=scheme_calendar, permissions=permissions,
            decision_at=recorded_at, now=recorded_at,
        )
    else:
        existing_eligibility = con.execute("""SELECT decision_at
            FROM mf_paper_systematic_redemption_eligibility WHERE instruction_id=?""",
            [instruction_id]).fetchone()
        eligibility_decision_at = (
            existing_eligibility[0] if existing_eligibility is not None else recorded_at
        )
        accepted_row = con.execute("""SELECT effective_at, received_at, basis,
            source_id, source_ref, source_sha256, reason FROM mf_paper_order_transitions
            WHERE order_id=? AND state='ACCEPTED' ORDER BY sequence DESC LIMIT 1""",
            [order_id]).fetchone()
        if accepted_row is None:
            raise MFSystematicFlowBookError("accepted fixed-INR redemption receipt is missing")
        acceptance = MFOrderAcknowledgement(
            order_id=order_id, state=MFOrderState.ACCEPTED, effective_at=accepted_row[0],
            received_at=accepted_row[1], basis=MFOrderEvidenceBasis(accepted_row[2]),
            source_id=accepted_row[3], source_ref=accepted_row[4],
            source_sha256=accepted_row[5], reason=accepted_row[6],
        )
        try:
            from ..kernel.mf_redemption_eligibility import resolve_redemption_eligible_nav
            eligibility = resolve_redemption_eligible_nav(
                order, acceptance, scheme_calendar, decision_at=eligibility_decision_at,
                permissions=permissions,
            )
        except ValueError as exc:
            raise MFSystematicFlowBookError(str(exc)) from exc
        evidence = json.dumps({
            field: getattr(eligibility, field)
            for field in eligibility.__dataclass_fields__
        }, sort_keys=True, separators=(",", ":"), default=str)
        con.execute("BEGIN TRANSACTION")
        try:
            old = con.execute("""SELECT order_id, requested_at, eligible_nav_date,
                transaction_at, decision_at, evidence_json, eligibility_sha256
                FROM mf_paper_systematic_redemption_eligibility WHERE instruction_id=?""",
                [instruction_id]).fetchone()
            expected = (order_id, requested_time, eligibility.eligible_nav_date,
                        eligibility.transaction_at, eligibility_decision_at, evidence,
                        eligibility.event_sha256)
            if old is not None:
                if tuple(old) != expected:
                    raise MFSystematicFlowBookError(
                        "fixed-INR redemption eligibility replay changed evidence"
                    )
            else:
                con.execute("""INSERT INTO mf_paper_systematic_redemption_eligibility (
                    instruction_id, order_id, requested_at, eligible_nav_date,
                    transaction_at, decision_at, evidence_json, eligibility_sha256,
                    created_at) VALUES (?,?,?,?,?,?,?,?,?)""",
                    [instruction_id, *expected, recorded_at])
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
    event = MFSystematicFlowEvent(
        event_id=f"{instruction.instruction_id}:redemption-requested",
        kind=MFSystematicFlowEventKind.REDEMPTION_REQUESTED,
        effective_at=requested_time,
        basis=MFSystematicFlowBasis.SIMULATED_ASSUMPTION,
        amount_inr=instruction.amount_inr, units=instruction.units,
    )
    lifecycle = _record_event_once(
        con, instruction_id=instruction_id, event=event, now=recorded_at,
        permissions=permissions,
    )
    eligible_nav_date: date | None
    if instruction.units is None:
        eligible_nav_date = eligibility.eligible_nav_date
    else:
        eligible_nav_date = settlement.get("eligible_nav_date")
    return {**settlement, **lifecycle, "settlement_id": settlement_id,
            "eligible_nav_date": eligible_nav_date}


def confirm_systematic_redemption_value(
    con,
    *,
    instruction_id: str,
    nav_observation_id: str,
    redemption_date: date,
    payout_due_date: date,
    decision_at: datetime,
    terms: SchemeCommercialTerms,
    permissions: tuple[DataPermission, ...],
    now: datetime,
    redemption_window_open: bool | None = None,
    gating_remaining_units: Decimal | str | int | None = None,
    segregated_portfolio_units: Decimal | str | int | None = None,
) -> dict[str, object]:
    """Value the periodic redemption through the FIFO lot book at an as-known NAV.

    NAV row identity, availability time, source hash, and effective paper-use
    permission are attached to the lifecycle event. The canonical redemption
    service is idempotent, allowing a restart before lifecycle persistence to
    resume without redeeming units twice. A fixed-INR instruction is treated as
    gross redemption notional: units are rounded down to the supported precision
    at the frozen eligible NAV, and applicable loads/charges reduce net payout.
    """
    from ..ingest.mfnav import as_known_on as mf_nav_as_known_on
    from .mf_lot_book import redeem
    from .mf_redemption_settlement import request_redemption, settlement_snapshot

    decision_time = _aware(decision_at, "decision_at")
    recorded_at = _aware(now, "now")
    if recorded_at < decision_time:
        raise MFSystematicFlowBookError("redemption persistence precedes decision time")
    if not nav_observation_id.strip():
        raise MFSystematicFlowBookError("NAV observation ID is required")
    row = con.execute(f"""SELECT {_I_COLUMNS} FROM mf_paper_systematic_instructions
        WHERE instruction_id=?""", [instruction_id]).fetchone()
    if row is None:
        raise MFSystematicFlowBookError("systematic instruction does not exist")
    instruction = _instruction_from_row(row)
    lifecycle_snapshot = systematic_instruction_snapshot(
        con, instruction_id=instruction_id,
    )
    if lifecycle_snapshot["stage"] not in (
            "REDEMPTION_REQUESTED", "VALUE_CONFIRMED", "PAYOUT_AVAILABLE",
            "PURCHASE_FUNDS_APPLIED", "ALLOTMENT_CONFIRMED"):
        raise MFSystematicFlowBookError(
            "systematic redemption must be requested before NAV value confirmation"
        )
    if (terms.scheme_code, terms.plan, terms.option, terms.isin) != (
            instruction.source_scheme, instruction.source_plan,
            instruction.source_option, instruction.source_isin):
        raise MFSystematicFlowBookError("redemption terms do not match the SWP/STP source")
    settlement_id = f"systematic:{instruction.instruction_id}"
    amount_eligibility = None
    if instruction.units is None:
        amount_eligibility = con.execute("""SELECT order_id, requested_at,
            eligible_nav_date, decision_at, evidence_json, eligibility_sha256
            FROM mf_paper_systematic_redemption_eligibility WHERE instruction_id=?""",
            [instruction_id]).fetchone()
        if amount_eligibility is None:
            raise MFSystematicFlowBookError(
                "fixed-INR redemption has no persisted accepted-order NAV eligibility"
            )
        if redemption_date != amount_eligibility[2]:
            raise MFSystematicFlowBookError(
                "redemption date differs from accepted fixed-INR order eligibility"
            )
        requested_at = amount_eligibility[1]
    else:
        settlement = settlement_snapshot(
            con, account_id=instruction.account_id, settlement_id=settlement_id,
        )
        if settlement["state"] not in (
                "REDEMPTION_PENDING", "PAYOUT_PENDING", "CASH_AVAILABLE"):
            raise MFSystematicFlowBookError("redemption is not awaiting NAV value confirmation")
        if Decimal(settlement["requested_units"]) != instruction.units:
            raise MFSystematicFlowBookError("reserved units differ from the systematic instruction")
        requested_at = settlement["requested_at"]

    observation = mf_nav_as_known_on(
        con, instruction.source_scheme, redemption_date, known_at=decision_time,
    )
    if observation is None or observation["observation_id"] != nav_observation_id:
        raise MFSystematicFlowBookError(
            "NAV must match the persisted as-known observation available at decision time"
        )
    nav_permissions = [permission for permission in permissions if permission.allows(
        source_id="AMFI", use=DataUse.PAPER_SIMULATION,
        on=decision_time.astimezone(IST).date(),
    )]
    if not nav_permissions:
        raise MFSystematicFlowBookError("AMFI NAV lacks effective PAPER_SIMULATION permission")

    requested_units = instruction.units
    if requested_units is None:
        nav = Decimal(str(observation["nav"]))
        assert instruction.amount_inr is not None
        requested_units = (instruction.amount_inr / nav).quantize(
            Decimal("0.000000000000000001"), rounding=ROUND_DOWN,
        )
        if requested_units <= 0:
            raise MFSystematicFlowBookError(
                "fixed-INR redemption amount is below one unit at the eligible NAV"
            )
        request_redemption(
            con, account_id=instruction.account_id, folio_id=instruction.folio_id,
            settlement_id=settlement_id,
            scheme_code=instruction.source_scheme, plan=instruction.source_plan,
            option=instruction.source_option, isin=instruction.source_isin,
            requested_units=requested_units,
            requested_on=requested_at.astimezone(IST).date(),
            requested_at=requested_at, now=recorded_at,
        )
        assert amount_eligibility is not None
        eligibility_evidence = amount_eligibility[4]
        con.execute("BEGIN TRANSACTION")
        try:
            current = con.execute("""SELECT eligible_nav_date, order_id,
                eligibility_sha256, eligibility_evidence_json
                FROM mf_paper_redemption_settlements
                WHERE account_id=? AND settlement_id=?""",
                [instruction.account_id, settlement_id]).fetchone()
            if current is None:
                raise MFSystematicFlowBookError("fixed-INR redemption reservation disappeared")
            expected_eligibility = (
                amount_eligibility[2], amount_eligibility[0],
                amount_eligibility[5], eligibility_evidence,
            )
            if current[2] is None:
                con.execute("""UPDATE mf_paper_redemption_settlements SET
                    eligible_nav_date=?, order_id=?, eligibility_sha256=?,
                    eligibility_evidence_json=?, updated_at=?
                    WHERE account_id=? AND settlement_id=?""",
                    [*expected_eligibility, recorded_at, instruction.account_id,
                     settlement_id])
            elif tuple(current) != expected_eligibility:
                raise MFSystematicFlowBookError(
                    "fixed-INR redemption reservation changed eligibility evidence"
                )
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
    settlement = settlement_snapshot(
        con, account_id=instruction.account_id, settlement_id=settlement_id,
    )
    if settlement["state"] not in (
            "REDEMPTION_PENDING", "PAYOUT_PENDING", "CASH_AVAILABLE"):
        raise MFSystematicFlowBookError("redemption is not awaiting NAV value confirmation")
    if Decimal(settlement["requested_units"]) != requested_units:
        raise MFSystematicFlowBookError("reserved units differ from the converted request")

    result = redeem(
        con, account_id=instruction.account_id, folio_id=instruction.folio_id,
        event_id=f"{instruction.instruction_id}:systematic-redemption",
        scheme_code=instruction.source_scheme, plan=instruction.source_plan,
        option=instruction.source_option, isin=instruction.source_isin,
        redemption_date=redemption_date,
        redemption_units=requested_units, nav_per_unit=observation["nav"],
        terms=terms, permissions=permissions, decision_at=decision_time,
        redemption_window_open=redemption_window_open,
        gating_remaining_units=gating_remaining_units,
        segregated_portfolio_units=segregated_portfolio_units,
        settlement_id=settlement_id, nav_observation_id=nav_observation_id,
        payout_due_date=payout_due_date, now=recorded_at,
    )
    event = MFSystematicFlowEvent(
        event_id=f"{instruction.instruction_id}:value-confirmed",
        kind=MFSystematicFlowEventKind.VALUE_CONFIRMED,
        effective_at=decision_time, basis=MFSystematicFlowBasis.OBSERVED_EVIDENCE,
        amount_inr=Decimal(result["net_inr"]),
        gross_inr=Decimal(result["gross_inr"]),
        exit_load_inr=Decimal(result["exit_load_inr"]),
        other_charges_inr=Decimal(result["other_charges_inr"]), source_id="AMFI",
        units=Decimal(result["units"]), nav_per_unit=Decimal(result["nav_per_unit"]),
        source_ref=nav_observation_id, source_sha256=observation["row_sha256"],
    )
    lifecycle = _record_event_once(
        con, instruction_id=instruction_id, event=event, now=recorded_at,
        permissions=permissions,
    )
    return {**result, **lifecycle, "nav_observation_id": nav_observation_id}


def release_systematic_payout(
    con,
    *,
    instruction_id: str,
    available_at: datetime,
    now: datetime,
    basis: MFSystematicFlowBasis = MFSystematicFlowBasis.SIMULATED_ASSUMPTION,
    evidence_ref: str | None = None,
    source_id: str | None = None,
    source_sha256: str | None = None,
    permissions: tuple[DataPermission, ...] = (),
) -> dict[str, object]:
    """Release the real paper-book receivable and append the matching SWP/STP event.

    The settlement cash write is idempotent. If a process stops before the
    systematic-event write, retry observes the same payout receipt and completes
    the lifecycle without crediting account cash twice.
    """
    from .mf_redemption_settlement import make_cash_available, settlement_snapshot

    payout_time = _aware(available_at, "available_at")
    recorded_at = _aware(now, "now")
    if recorded_at < payout_time:
        raise MFSystematicFlowBookError("payout persistence precedes payout availability")
    try:
        payout_basis = MFSystematicFlowBasis(basis)
    except ValueError as exc:
        raise MFSystematicFlowBookError("unknown payout evidence basis") from exc
    row = con.execute(f"""SELECT {_I_COLUMNS} FROM mf_paper_systematic_instructions
        WHERE instruction_id=?""", [instruction_id]).fetchone()
    if row is None:
        raise MFSystematicFlowBookError("systematic instruction does not exist")
    instruction = _instruction_from_row(row)
    settlement_id = f"systematic:{instruction.instruction_id}"
    settlement = settlement_snapshot(
        con, account_id=instruction.account_id, settlement_id=settlement_id,
    )
    if settlement["net_inr"] is None:
        raise MFSystematicFlowBookError("redemption value has not been confirmed")

    raw_events = con.execute("""SELECT event_id, kind, effective_at, basis, amount_inr,
        gross_inr, exit_load_inr, other_charges_inr, nav_per_unit, units, reason,
        source_id, source_ref, source_sha256 FROM mf_paper_systematic_events
        WHERE instruction_id=? ORDER BY sequence""", [instruction_id]).fetchall()
    events = tuple(_event_from_row(event_row) for event_row in raw_events)
    _ = MFSystematicFlowLifecycle(instruction, events)
    if not events or events[-1].kind is not MFSystematicFlowEventKind.VALUE_CONFIRMED:
        raise MFSystematicFlowBookError(
            "systematic payout requires a recorded value-confirmation event"
        )
    if events[-1].amount != Decimal(settlement["net_inr"]):
        raise MFSystematicFlowBookError(
            "MF settlement net value differs from the systematic value-confirmation event"
        )

    released = make_cash_available(
        con, account_id=instruction.account_id, settlement_id=settlement_id,
        available_at=payout_time, basis=payout_basis.value,
        evidence_ref=evidence_ref, source_id=source_id, source_sha256=source_sha256,
        permissions=permissions, now=recorded_at,
    )
    event = MFSystematicFlowEvent(
        event_id=f"{instruction.instruction_id}:payout-available",
        kind=MFSystematicFlowEventKind.PAYOUT_AVAILABLE,
        effective_at=payout_time, basis=payout_basis,
        amount_inr=Decimal(released["net_inr"]), source_id=source_id,
        source_ref=evidence_ref, source_sha256=source_sha256,
    )
    lifecycle_result = _record_event_once(
        con, instruction_id=instruction_id, event=event, now=recorded_at,
        permissions=permissions,
    )
    return {**released, **lifecycle_result, "settlement_id": settlement_id}


def apply_systematic_transfer_purchase(
    con,
    *,
    instruction_id: str,
    investment_inr: Decimal | str | int,
    nav_per_unit: Decimal | str | int,
    nav_observation_id: str,
    terms: SchemeCommercialTerms,
    permissions: tuple[DataPermission, ...],
    decision_at: datetime,
    allotted_on: date,
    now: datetime,
    lock_in_until: date | None = None,
) -> dict[str, object]:
    """Reject the legacy all-at-once STP shortcut.

    STP purchases must use the reserve and allot stages so funds remain
    separately visible while the accepted target order is NAV_PENDING.
    """
    del (con, instruction_id, investment_inr, nav_per_unit, nav_observation_id,
         terms, permissions, decision_at, allotted_on, now, lock_in_until)
    raise MFSystematicFlowBookError(
        "single-call STP purchase is disabled; reserve funds, then allot at eligible NAV"
    )

def reserve_systematic_transfer_purchase(
    con,
    *,
    instruction_id: str,
    investment_inr: Decimal | str | int,
    scheme_calendar: SchemeTransactionCalendar,
    permissions: tuple[DataPermission, ...],
    requested_at: datetime,
    accepted_at: datetime,
    initiated_at: datetime,
    credited_at: datetime,
    available_at: datetime,
    decision_at: datetime,
    now: datetime,
) -> dict[str, object]:
    """Accept an STP target order and reserve payout cash until NAV/allotment.

    The acceptance, payment clocks and NAV eligibility are persisted separately.
    Funds move from CASH to SIP_PAYMENT_PENDING only after utilization availability;
    they are never treated as invested while the order remains NAV_PENDING.
    """
    from ..kernel.mf_purchase_payment import (
        MFPurchasePaymentMilestone, PaymentEvidenceBasis, PaymentMilestoneKind,
    )
    from .mf_account_ledger import post_journal
    from .mf_order_book import record_response, submit_order
    from .mf_purchase_book import record_payment_milestone, resolve_eligible_nav

    clocks = {
        "requested_at": _aware(requested_at, "requested_at"),
        "accepted_at": _aware(accepted_at, "accepted_at"),
        "initiated_at": _aware(initiated_at, "initiated_at"),
        "credited_at": _aware(credited_at, "credited_at"),
        "available_at": _aware(available_at, "available_at"),
        "decision_at": _aware(decision_at, "decision_at"),
        "now": _aware(now, "now"),
    }
    if not (clocks["accepted_at"] >= clocks["requested_at"]
            and clocks["initiated_at"] >= clocks["requested_at"]
            and clocks["credited_at"] >= clocks["initiated_at"]
            and clocks["available_at"] >= clocks["credited_at"]
            and clocks["decision_at"] >= clocks["available_at"]
            and clocks["now"] >= clocks["decision_at"]):
        raise MFSystematicFlowBookError("STP order and payment clocks must be causal")
    amount = Decimal(str(investment_inr))
    if not amount.is_finite() or amount <= 0:
        raise MFSystematicFlowBookError("STP purchase amount must be positive and finite")
    row = con.execute(f"""SELECT {_I_COLUMNS} FROM mf_paper_systematic_instructions
        WHERE instruction_id=?""", [instruction_id]).fetchone()
    if row is None:
        raise MFSystematicFlowBookError("systematic instruction does not exist")
    instruction = _instruction_from_row(row)
    if instruction.kind is not MFSystematicFlowKind.STP:
        raise MFSystematicFlowBookError("target purchase is only valid for an STP instruction")
    if clocks["requested_at"].astimezone(IST).date() < instruction.scheduled_for:
        raise MFSystematicFlowBookError("STP target order precedes its scheduled date")
    events = tuple(_event_from_row(item) for item in con.execute("""SELECT event_id, kind,
        effective_at, basis, amount_inr, gross_inr, exit_load_inr, other_charges_inr,
        nav_per_unit, units, reason, source_id, source_ref, source_sha256
        FROM mf_paper_systematic_events WHERE instruction_id=? ORDER BY sequence""",
        [instruction_id]).fetchall())
    lifecycle = MFSystematicFlowLifecycle(instruction, events)
    if len(events) < 3 or events[2].kind is not MFSystematicFlowEventKind.PAYOUT_AVAILABLE:
        raise MFSystematicFlowBookError("STP target purchase requires settled redemption proceeds")
    if clocks["requested_at"] <= events[2].effective_at:
        raise MFSystematicFlowBookError("STP target order must follow available payout")
    funding = next((item for item in events if item.kind is
                    MFSystematicFlowEventKind.PURCHASE_FUNDS_APPLIED), None)
    if funding is not None and funding.amount != amount:
        raise MFSystematicFlowBookError("STP retry changed the applied purchase amount")
    if funding is None and amount > lifecycle.available_to_redeploy_inr:
        raise MFSystematicFlowBookError("STP purchase exceeds settled redemption proceeds")
    reservation_id = f"systematic-stp-funds:{instruction_id}"
    journal_exists = con.execute("""SELECT COUNT(*) FROM mf_paper_ledger_entries
        WHERE account_id=? AND transaction_id=?""",
        [instruction.account_id, reservation_id]).fetchone()[0]
    if not journal_exists:
        current_cash = con.execute("SELECT cash_inr FROM mf_paper_accounts WHERE account_id=?",
                                   [instruction.account_id]).fetchone()
        if current_cash is None or Decimal(current_cash[0]) < amount:
            raise MFSystematicFlowBookError("available paper cash is insufficient for STP reservation")

    order_id = f"{instruction.instruction_id}:stp-order"
    order = MFOrder(
        order_id=order_id, account_id=instruction.account_id, folio_id=instruction.folio_id,
        scheme_code=instruction.target_scheme or "", plan=instruction.target_plan or "",
        option=instruction.target_option or "", transaction_type=MFOrderKind.SWITCH_IN,
        requested_at=clocks["requested_at"], amount_inr=amount,
    )
    submit_order(con, order, valid_business_days=1, calendar=scheme_calendar,
                 now=clocks["now"])
    record_response(con, MFOrderAcknowledgement(
        order_id=order_id, state=MFOrderState.ACCEPTED,
        effective_at=clocks["accepted_at"], received_at=clocks["accepted_at"],
        basis=MFOrderEvidenceBasis.SIMULATED_ASSUMPTION,
    ), permissions=permissions, now=clocks["now"])
    for milestone_kind, effective in (
        (PaymentMilestoneKind.INITIATED, clocks["initiated_at"]),
        (PaymentMilestoneKind.CREDITED_TO_SCHEME, clocks["credited_at"]),
        (PaymentMilestoneKind.AVAILABLE_FOR_UTILIZATION, clocks["available_at"]),
    ):
        record_payment_milestone(
            con, order_id=order_id,
            milestone=MFPurchasePaymentMilestone(
                kind=milestone_kind, effective_at=effective, received_at=effective,
                basis=PaymentEvidenceBasis.SIMULATED_ASSUMPTION,
            ), now=clocks["now"],
        )
    eligibility = resolve_eligible_nav(
        con, order_id=order_id, scheme_calendar=scheme_calendar,
        permissions=permissions, decision_at=clocks["decision_at"], now=clocks["now"],
    )

    # The journal is the idempotency boundary for the account balance mutation.
    # If a crash occurs before the lifecycle event, replay sees the journal and
    # appends the missing event without debiting cash twice.
    transaction_id = reservation_id
    con.execute("BEGIN TRANSACTION")
    try:
        posted = con.execute("""SELECT COUNT(*) FROM mf_paper_ledger_entries
            WHERE account_id=? AND transaction_id=?""",
            [instruction.account_id, transaction_id]).fetchone()[0]
        if not posted:
            cash = con.execute("SELECT cash_inr FROM mf_paper_accounts WHERE account_id=?",
                                [instruction.account_id]).fetchone()
            if cash is None or Decimal(cash[0]) < amount:
                raise MFSystematicFlowBookError("available paper cash is insufficient for STP reservation")
            con.execute("UPDATE mf_paper_accounts SET cash_inr=cash_inr-? WHERE account_id=?",
                        [amount, instruction.account_id])
            post_journal(
                con, account_id=instruction.account_id, transaction_id=transaction_id,
                effective_at=clocks["available_at"], recorded_at=clocks["now"],
                entries=(("SIP_PAYMENT_PENDING", amount, 0), ("CASH", 0, amount)),
                evidence={"instruction_id": instruction_id, "order_id": order_id,
                          "eligibility_sha256": eligibility["eligibility_sha256"],
                          "purpose": "STP_TARGET_NAV_PENDING"},
            )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    funding_event = MFSystematicFlowEvent(
        event_id=f"{instruction_id}:purchase-funds-applied",
        kind=MFSystematicFlowEventKind.PURCHASE_FUNDS_APPLIED,
        effective_at=clocks["available_at"],
        basis=MFSystematicFlowBasis.SIMULATED_ASSUMPTION, amount_inr=amount,
    )
    recorded = _record_event_once(
        con, instruction_id=instruction_id, event=funding_event,
        now=clocks["now"], permissions=permissions,
    )
    return {"instruction_id": instruction_id, "order_id": order_id,
            "state": eligibility["state"], "eligible_nav_date": eligibility["eligible_nav_date"],
            "amount_inr": amount, **recorded}


def allot_systematic_transfer_purchase(
    con,
    *,
    instruction_id: str,
    nav_observation_id: str,
    nav_per_unit: Decimal | str | int,
    terms: SchemeCommercialTerms,
    permissions: tuple[DataPermission, ...],
    decision_at: datetime,
    allotted_at: datetime,
    now: datetime,
    lock_in_until: date | None = None,
) -> dict[str, object]:
    """Allot a reserved STP target only at its frozen, as-known eligible NAV."""
    from .mf_purchase_book import apply_allotment

    row = con.execute(f"""SELECT {_I_COLUMNS} FROM mf_paper_systematic_instructions
        WHERE instruction_id=?""", [instruction_id]).fetchone()
    if row is None:
        raise MFSystematicFlowBookError("systematic instruction does not exist")
    instruction = _instruction_from_row(row)
    if instruction.kind is not MFSystematicFlowKind.STP:
        raise MFSystematicFlowBookError("target allotment is only valid for an STP instruction")
    events = tuple(_event_from_row(item) for item in con.execute("""SELECT event_id, kind,
        effective_at, basis, amount_inr, gross_inr, exit_load_inr, other_charges_inr,
        nav_per_unit, units, reason, source_id, source_ref, source_sha256
        FROM mf_paper_systematic_events WHERE instruction_id=? ORDER BY sequence""",
        [instruction_id]).fetchall())
    lifecycle = MFSystematicFlowLifecycle(instruction, events)
    funding = next((item for item in events if item.kind is
                    MFSystematicFlowEventKind.PURCHASE_FUNDS_APPLIED), None)
    if funding is None:
        raise MFSystematicFlowBookError("STP target funds must be reserved before allotment")
    if any(item.kind is MFSystematicFlowEventKind.PURCHASE_CANCELLED for item in events):
        raise MFSystematicFlowBookError("cancelled STP target cannot be allotted")
    if any(item.kind is MFSystematicFlowEventKind.ALLOTMENT_CONFIRMED for item in events):
        prior = next(item for item in events if item.kind is MFSystematicFlowEventKind.ALLOTMENT_CONFIRMED)
        if (prior.source_ref != nav_observation_id
                or prior.nav_per_unit != Decimal(str(nav_per_unit))
                or prior.amount_inr != funding.amount
                or prior.effective_at != _aware(allotted_at, "allotted_at")):
            raise MFSystematicFlowBookError("STP allotment replay changed NAV evidence")
        return {"instruction_id": instruction_id, "order_id": f"{instruction_id}:stp-order",
                "state": "ALLOTTED", "units": prior.units, "stage": lifecycle.stage,
                "status": "already_recorded"}
    allotment = apply_allotment(
        con, order_id=f"{instruction_id}:stp-order",
        nav_observation_id=nav_observation_id, nav_per_unit=nav_per_unit,
        terms=terms, permissions=permissions, decision_at=decision_at,
        allotted_at=allotted_at, now=now, lock_in_until=lock_in_until,
    )
    event = MFSystematicFlowEvent(
        event_id=f"{instruction_id}:allotment-confirmed",
        kind=MFSystematicFlowEventKind.ALLOTMENT_CONFIRMED,
        effective_at=_aware(allotted_at, "allotted_at"),
        basis=MFSystematicFlowBasis.OBSERVED_EVIDENCE,
        amount_inr=funding.amount, nav_per_unit=Decimal(str(nav_per_unit)),
        units=Decimal(allotment["units"]), source_id="AMFI",
        source_ref=nav_observation_id,
        source_sha256=con.execute("""SELECT row_sha256 FROM mf_nav_observations
            WHERE observation_id=?""", [nav_observation_id]).fetchone()[0],
    )
    result = _record_event_once(
        con, instruction_id=instruction_id, event=event,
        now=_aware(now, "now"), permissions=permissions,
    )
    return {"instruction_id": instruction_id, "order_id": f"{instruction_id}:stp-order",
            **allotment, **result}


def cancel_systematic_transfer_purchase(
    con,
    *,
    instruction_id: str,
    cancelled_at: datetime,
    now: datetime,
    reason: str,
) -> dict[str, object]:
    """Cancel a NAV-pending STP purchase and release reserved cash once."""
    from .mf_account_ledger import post_journal

    cancelled = _aware(cancelled_at, "cancelled_at")
    recorded = _aware(now, "now")
    if recorded < cancelled or not reason.strip():
        raise MFSystematicFlowBookError("cancellation needs a reason and ordered timestamps")
    row = con.execute(f"""SELECT {_I_COLUMNS} FROM mf_paper_systematic_instructions
        WHERE instruction_id=?""", [instruction_id]).fetchone()
    if row is None:
        raise MFSystematicFlowBookError("systematic instruction does not exist")
    instruction = _instruction_from_row(row)
    if instruction.kind is not MFSystematicFlowKind.STP:
        raise MFSystematicFlowBookError("target purchase cancellation is only valid for an STP")
    events = tuple(_event_from_row(item) for item in con.execute("""SELECT event_id, kind,
        effective_at, basis, amount_inr, gross_inr, exit_load_inr, other_charges_inr,
        nav_per_unit, units, reason, source_id, source_ref, source_sha256
        FROM mf_paper_systematic_events WHERE instruction_id=? ORDER BY sequence""",
        [instruction_id]).fetchall())
    lifecycle = MFSystematicFlowLifecycle(instruction, events)
    if events and events[-1].kind is MFSystematicFlowEventKind.PURCHASE_CANCELLED:
        if events[-1].reason != reason or events[-1].effective_at != cancelled:
            raise MFSystematicFlowBookError("STP cancellation replay changed evidence")
        return {"instruction_id": instruction_id, "stage": lifecycle.stage,
                "status": "already_cancelled"}
    funding = next((item for item in events if item.kind is
                    MFSystematicFlowEventKind.PURCHASE_FUNDS_APPLIED), None)
    if funding is None:
        raise MFSystematicFlowBookError("STP target has no reserved purchase funds to release")
    if cancelled <= funding.effective_at:
        raise MFSystematicFlowBookError("STP cancellation must follow target-funds reservation")
    if any(item.kind is MFSystematicFlowEventKind.ALLOTMENT_CONFIRMED for item in events):
        raise MFSystematicFlowBookError("allotted STP target cannot be cancelled")
    state = con.execute("""SELECT state FROM mf_paper_purchase_eligibility
        WHERE order_id=?""", [f"{instruction_id}:stp-order"]).fetchone()
    if state is None or state[0] not in ("NAV_PENDING", "CANCELLED"):
        raise MFSystematicFlowBookError("only a NAV_PENDING STP target may be cancelled")
    transaction_id = f"systematic-stp-cancel:{instruction_id}"
    con.execute("BEGIN TRANSACTION")
    try:
        posted = con.execute("""SELECT COUNT(*) FROM mf_paper_ledger_entries
            WHERE account_id=? AND transaction_id=?""",
            [instruction.account_id, transaction_id]).fetchone()[0]
        if not posted:
            if state[0] == "CANCELLED":
                raise MFSystematicFlowBookError(
                    "cancelled STP target is missing its cash-release journal"
                )
            con.execute("UPDATE mf_paper_accounts SET cash_inr=cash_inr+? WHERE account_id=?",
                        [funding.amount, instruction.account_id])
            post_journal(
                con, account_id=instruction.account_id, transaction_id=transaction_id,
                effective_at=cancelled, recorded_at=recorded,
                entries=(("CASH", funding.amount, 0), ("SIP_PAYMENT_PENDING", 0,
                                                         funding.amount)),
                evidence={"instruction_id": instruction_id,
                          "order_id": f"{instruction_id}:stp-order",
                          "reason": reason, "purpose": "STP_TARGET_CANCELLED"},
            )
            con.execute("""UPDATE mf_paper_purchase_eligibility SET state='CANCELLED',
                updated_at=? WHERE order_id=? AND state='NAV_PENDING'""",
                [recorded, f"{instruction_id}:stp-order"])
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    cancellation = MFSystematicFlowEvent(
        event_id=f"{instruction_id}:purchase-cancelled",
        kind=MFSystematicFlowEventKind.PURCHASE_CANCELLED,
        effective_at=cancelled, basis=MFSystematicFlowBasis.SIMULATED_ASSUMPTION,
        amount_inr=funding.amount, reason=reason,
    )
    return _record_event_once(
        con, instruction_id=instruction_id, event=cancellation,
        now=recorded, permissions=(),
    )


def systematic_instruction_snapshot(con, *, instruction_id: str) -> dict[str, object]:
    row = con.execute(f"""SELECT {_I_COLUMNS} FROM mf_paper_systematic_instructions
        WHERE instruction_id=?""", [instruction_id]).fetchone()
    if row is None:
        raise MFSystematicFlowBookError("systematic instruction does not exist")
    instruction = _instruction_from_row(row)
    raw = con.execute("""SELECT event_id, kind, effective_at, basis, amount_inr,
        gross_inr, exit_load_inr, other_charges_inr, nav_per_unit, units, reason,
        source_id, source_ref, source_sha256, recorded_at, event_sha256, evidence_json
        FROM mf_paper_systematic_events WHERE instruction_id=? ORDER BY sequence""",
        [instruction_id]).fetchall()
    events = tuple(_event_from_row(event_row) for event_row in raw)
    lifecycle = MFSystematicFlowLifecycle(instruction, events)
    return {"instruction": instruction, "stage": lifecycle.stage,
            "available_to_redeploy_inr": lifecycle.available_to_redeploy_inr,
            "state_sha256": row[17], "events": raw}


__all__ = [
    "MFSystematicFlowBookError", "create_systematic_plan", "record_systematic_event",
    "apply_systematic_transfer_purchase", "confirm_systematic_redemption_value",
    "allot_systematic_transfer_purchase", "cancel_systematic_transfer_purchase",
    "release_systematic_payout", "request_systematic_redemption",
    "reserve_systematic_transfer_purchase",
    "systematic_instruction_snapshot",
]
