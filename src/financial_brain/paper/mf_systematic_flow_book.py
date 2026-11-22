"""Durable SWP/STP paper instructions and append-only settlement events."""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from ..forward_edge.data_permissions import DataPermission, DataUse
from ..providers.mf_scheme_terms import SchemeCommercialTerms
from ..kernel.mf_systematic_flows import (
    MFSystematicFlowBasis, MFSystematicFlowError, MFSystematicFlowEvent,
    MFSystematicFlowEventKind, MFSystematicFlowInstruction,
    MFSystematicFlowKind, MFSystematicFlowLifecycle,
)
from ..kernel.trading_clock import IST


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
    permissions: tuple[DataPermission, ...] = (),
) -> dict[str, object]:
    """Create the canonical reserved-unit redemption for a periodic MF instruction.

    This first execution adapter supports unit-denominated SWP/STP instructions.
    INR-denominated instructions still need a scheme-specific amount-to-units
    conversion at the order date and are rejected instead of guessed.
    The underlying redemption request is idempotent, so a restart between the
    settlement write and lifecycle-event write can safely call this again.
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
    if instruction.units is None:
        raise MFSystematicFlowBookError(
            "amount-denominated systematic redemption needs an explicit amount-to-units adapter"
        )
    from .mf_redemption_settlement import request_redemption

    settlement_id = f"systematic:{instruction.instruction_id}"
    settlement = request_redemption(
        con, account_id=instruction.account_id, settlement_id=settlement_id,
        scheme_code=instruction.source_scheme, plan=instruction.source_plan,
        option=instruction.source_option, isin=instruction.source_isin,
        requested_units=instruction.units,
        requested_on=requested_time.astimezone(IST).date(),
        requested_at=requested_time, now=recorded_at,
    )
    event = MFSystematicFlowEvent(
        event_id=f"{instruction.instruction_id}:redemption-requested",
        kind=MFSystematicFlowEventKind.REDEMPTION_REQUESTED,
        effective_at=requested_time,
        basis=MFSystematicFlowBasis.SIMULATED_ASSUMPTION,
        units=instruction.units,
    )
    lifecycle = _record_event_once(
        con, instruction_id=instruction_id, event=event, now=recorded_at,
        permissions=permissions,
    )
    return {**settlement, **lifecycle, "settlement_id": settlement_id}


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
    resume without redeeming units twice.
    """
    from ..ingest.mfnav import as_known_on as mf_nav_as_known_on
    from .mf_lot_book import redeem
    from .mf_redemption_settlement import settlement_snapshot

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
    if instruction.units is None:
        raise MFSystematicFlowBookError(
            "amount-denominated systematic redemption needs an explicit amount-to-units adapter"
        )
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
    settlement = settlement_snapshot(
        con, account_id=instruction.account_id, settlement_id=settlement_id,
    )
    if settlement["state"] not in (
            "REDEMPTION_PENDING", "PAYOUT_PENDING", "CASH_AVAILABLE"):
        raise MFSystematicFlowBookError("redemption is not awaiting NAV value confirmation")
    if Decimal(settlement["requested_units"]) != instruction.units:
        raise MFSystematicFlowBookError("reserved units differ from the systematic instruction")

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

    result = redeem(
        con, account_id=instruction.account_id,
        event_id=f"{instruction.instruction_id}:systematic-redemption",
        scheme_code=instruction.source_scheme, plan=instruction.source_plan,
        option=instruction.source_option, isin=instruction.source_isin,
        redemption_date=redemption_date,
        redemption_units=settlement["requested_units"], nav_per_unit=observation["nav"],
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
        other_charges_inr=Decimal(0), source_id="AMFI",
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
    """Apply an STP target subscription to the canonical cash and lot book.

    This synchronous paper adapter submits a target purchase only after payout
    has reached canonical cash. A retry reuses deterministic event/lot IDs and
    lets the subscription book enforce idempotency before appending lifecycle
    funding and allotment records.
    """
    from ..ingest.mfnav import as_known_on as mf_nav_as_known_on
    from .mf_lot_book import subscribe

    decision_time = _aware(decision_at, "decision_at")
    allotment_time = _aware(now, "now")
    amount = Decimal(str(investment_inr))
    nav = Decimal(str(nav_per_unit))
    if not amount.is_finite() or amount <= 0:
        raise MFSystematicFlowBookError("STP purchase amount must be positive and finite")
    if not nav.is_finite() or nav <= 0:
        raise MFSystematicFlowBookError("STP NAV must be positive and finite")
    if allotment_time <= decision_time:
        raise MFSystematicFlowBookError("STP allotment must follow purchase-funds application")

    row = con.execute(f"""SELECT {_I_COLUMNS} FROM mf_paper_systematic_instructions
        WHERE instruction_id=?""", [instruction_id]).fetchone()
    if row is None:
        raise MFSystematicFlowBookError("systematic instruction does not exist")
    instruction = _instruction_from_row(row)
    if instruction.kind is not MFSystematicFlowKind.STP:
        raise MFSystematicFlowBookError("target purchase is only valid for an STP instruction")
    if (terms.scheme_code, terms.plan, terms.option, terms.isin) != (
            instruction.target_scheme, instruction.target_plan,
            instruction.target_option, instruction.target_isin):
        raise MFSystematicFlowBookError("target purchase terms do not match the STP instruction")
    if not nav_observation_id.strip():
        raise MFSystematicFlowBookError("STP target NAV observation ID is required")
    observation = mf_nav_as_known_on(
        con, instruction.target_scheme or "", allotted_on, known_at=decision_time,
    )
    if (observation is None or observation["observation_id"] != nav_observation_id
            or Decimal(str(observation["nav"])) != nav):
        raise MFSystematicFlowBookError(
            "STP NAV must match the persisted as-known target-scheme observation"
        )
    if not any(permission.allows(
            source_id="AMFI", use=DataUse.PAPER_SIMULATION,
            on=decision_time.astimezone(IST).date()) for permission in permissions):
        raise MFSystematicFlowBookError("AMFI STP NAV lacks effective PAPER_SIMULATION permission")

    raw_events = con.execute("""SELECT event_id, kind, effective_at, basis, amount_inr,
        gross_inr, exit_load_inr, other_charges_inr, nav_per_unit, units, reason,
        source_id, source_ref, source_sha256 FROM mf_paper_systematic_events
        WHERE instruction_id=? ORDER BY sequence""", [instruction_id]).fetchall()
    events = tuple(_event_from_row(event_row) for event_row in raw_events)
    lifecycle = MFSystematicFlowLifecycle(instruction, events)
    if len(events) < 3 or events[2].kind is not MFSystematicFlowEventKind.PAYOUT_AVAILABLE:
        raise MFSystematicFlowBookError("STP target purchase requires settled redemption proceeds")
    prior_funding = next((event for event in events if
        event.kind is MFSystematicFlowEventKind.PURCHASE_FUNDS_APPLIED), None)
    if prior_funding is None:
        if amount > lifecycle.available_to_redeploy_inr:
            raise MFSystematicFlowBookError("STP purchase exceeds settled redemption proceeds")
    elif prior_funding.amount != amount:
        raise MFSystematicFlowBookError("STP retry changed the applied purchase amount")

    subscription = subscribe(
        con, account_id=instruction.account_id,
        event_id=f"{instruction.instruction_id}:stp-subscription",
        lot_id=f"{instruction.instruction_id}:stp-lot",
        scheme_code=instruction.target_scheme or "",
        plan=instruction.target_plan or "", option=instruction.target_option or "",
        isin=instruction.target_isin or "", allotted_on=allotted_on,
        lock_in_until=lock_in_until, investment_inr=amount, nav_per_unit=nav,
        exit_loads=terms.exit_loads,
        exit_loads_sha256=terms.source_sha256 if terms.exit_loads is not None else None,
        terms=terms, permissions=permissions, decision_at=decision_time,
        now=allotment_time,
    )
    funding_event = MFSystematicFlowEvent(
        event_id=f"{instruction.instruction_id}:purchase-funds-applied",
        kind=MFSystematicFlowEventKind.PURCHASE_FUNDS_APPLIED,
        effective_at=decision_time, basis=MFSystematicFlowBasis.SIMULATED_ASSUMPTION,
        amount_inr=amount,
    )
    if prior_funding is not None:
        if (prior_funding.effective_at != funding_event.effective_at
                or prior_funding.basis is not funding_event.basis):
            raise MFSystematicFlowBookError("STP retry changed purchase-funds evidence")
        funding_result: dict[str, object] = {
            "instruction_id": instruction_id, "stage": lifecycle.stage,
            "status": "already_recorded",
        }
    else:
        funding_result = record_systematic_event(
            con, instruction_id=instruction_id, event=funding_event,
            now=allotment_time, permissions=permissions,
        )
    units = Decimal(subscription["units"])
    allotment_event = MFSystematicFlowEvent(
        event_id=f"{instruction.instruction_id}:allotment-confirmed",
        kind=MFSystematicFlowEventKind.ALLOTMENT_CONFIRMED,
        effective_at=allotment_time, basis=MFSystematicFlowBasis.OBSERVED_EVIDENCE,
        amount_inr=amount, nav_per_unit=nav, units=units,
        source_id="AMFI", source_ref=nav_observation_id,
        source_sha256=observation["row_sha256"],
    )
    prior_allotment = next((event for event in events if
        event.kind is MFSystematicFlowEventKind.ALLOTMENT_CONFIRMED), None)
    if prior_allotment is not None:
        if (prior_allotment.effective_at != allotment_event.effective_at
                or prior_allotment.amount_inr != allotment_event.amount_inr
                or prior_allotment.nav_per_unit != allotment_event.nav_per_unit
                or prior_allotment.units != allotment_event.units
                or prior_allotment.basis is not allotment_event.basis
                or prior_allotment.source_ref != allotment_event.source_ref
                or prior_allotment.source_sha256 != allotment_event.source_sha256):
            raise MFSystematicFlowBookError("STP retry changed allotment evidence")
        allotment_result: dict[str, object] = {
            "instruction_id": instruction_id, "stage": lifecycle.stage,
            "status": "already_recorded",
        }
    else:
        allotment_result = record_systematic_event(
            con, instruction_id=instruction_id, event=allotment_event,
            now=allotment_time, permissions=permissions,
        )
    return {**subscription, **funding_result, **allotment_result}


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
    "release_systematic_payout", "request_systematic_redemption",
    "systematic_instruction_snapshot",
]
