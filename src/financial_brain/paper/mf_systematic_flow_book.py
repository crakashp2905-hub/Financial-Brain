"""Durable SWP/STP paper instructions and append-only settlement events."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from ..forward_edge.data_permissions import DataPermission, DataUse
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


def _instruction_payload(item: MFSystematicFlowInstruction) -> dict[str, Any]:
    return {
        "instruction_id": item.instruction_id, "plan_id": item.plan_id,
        "account_id": item.account_id, "folio_id": item.folio_id,
        "kind": item.kind.value, "scheduled_for": item.scheduled_for.isoformat(),
        "source_scheme": item.source_scheme, "source_plan": item.source_plan,
        "source_option": item.source_option, "source_isin": item.source_isin,
        "amount_inr": str(item.amount_inr) if item.amount_inr is not None else None,
        "units": str(item.units) if item.units is not None else None,
        "target_scheme": item.target_scheme, "target_plan": item.target_plan,
        "target_option": item.target_option, "target_isin": item.target_isin,
    }


def _event_payload(event: MFSystematicFlowEvent) -> dict[str, Any]:
    return {
        "event_id": event.event_id, "kind": event.kind.value,
        "effective_at": event.effective_at.isoformat(), "basis": event.basis.value,
        "amount_inr": str(event.amount_inr) if event.amount_inr is not None else None,
        "gross_inr": str(event.gross_inr) if event.gross_inr is not None else None,
        "exit_load_inr": str(event.exit_load_inr) if event.exit_load_inr is not None else None,
        "other_charges_inr": str(event.other_charges_inr) if event.other_charges_inr is not None else None,
        "nav_per_unit": str(event.nav_per_unit) if event.nav_per_unit is not None else None,
        "units": str(event.units) if event.units is not None else None,
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
    "systematic_instruction_snapshot",
]
