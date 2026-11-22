"""Durable paper persistence for SIP schedules and independent installments."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from ..kernel.mf_sip import (
    MFSIPError, MFSIPInstallment, MFSIPSeries, MFSIPState,
    validate_installment_transition,
)


class MFSIPBookError(ValueError):
    """Invalid or conflicting SIP paper-book operation."""


def _aware(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise MFSIPBookError(f"{label} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _snapshot_payload(item: MFSIPInstallment) -> dict[str, object]:
    return {
        "installment_id": item.installment_id, "plan_id": item.plan_id,
        "account_id": item.account_id, "folio_id": item.folio_id,
        "scheme_code": item.scheme_code, "plan": item.plan, "option": item.option,
        "scheduled_for": item.scheduled_for.isoformat(), "amount_inr": str(item.amount_inr),
        "state": item.state.value,
        "payment_initiated_at": item.payment_initiated_at.isoformat() if item.payment_initiated_at else None,
        "funds_available_at": item.funds_available_at.isoformat() if item.funds_available_at else None,
        "missed_at": item.missed_at.isoformat() if item.missed_at else None,
        "miss_reason": item.miss_reason,
        "nav_date": item.nav_date.isoformat() if item.nav_date else None,
        "nav_per_unit": str(item.nav_per_unit) if item.nav_per_unit is not None else None,
        "nav_published_at": item.nav_published_at.isoformat() if item.nav_published_at else None,
        "nav_available_at": item.nav_available_at.isoformat() if item.nav_available_at else None,
        "allotted_at": item.allotted_at.isoformat() if item.allotted_at else None,
        "units_allotted": str(item.units_allotted) if item.units_allotted is not None else None,
    }


def _state_hash(item: MFSIPInstallment) -> str:
    encoded = json.dumps(_snapshot_payload(item), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _from_row(row) -> MFSIPInstallment:
    return MFSIPInstallment(
        installment_id=row[0], plan_id=row[1], account_id=row[2], folio_id=row[3],
        scheme_code=row[4], plan=row[5], option=row[6], scheduled_for=row[7],
        amount_inr=row[8], state=MFSIPState(row[9]), payment_initiated_at=row[10],
        funds_available_at=row[11], missed_at=row[12], miss_reason=row[13],
        nav_date=row[14], nav_per_unit=row[15], nav_published_at=row[16],
        nav_available_at=row[17], allotted_at=row[18], units_allotted=row[19],
    )


_COLUMNS = """installment_id, plan_id, account_id, folio_id, scheme_code, plan,
    option, scheduled_for, amount_inr, current_state, payment_initiated_at,
    funds_available_at, missed_at, miss_reason, nav_date, nav_per_unit,
    nav_published_at, nav_available_at, allotted_at, units_allotted, state_sha256,
    created_at, updated_at"""


def create_sip_series(con, series: MFSIPSeries, *, now: datetime) -> dict[str, object]:
    """Persist a frozen schedule, with one initial event for each scheduled installment."""
    recorded_at = _aware(now, "now")
    if not series.installments:
        raise MFSIPBookError("SIP series must contain at least one installment")
    first = series.installments[0]
    for item in series.installments:
        if item.state is not MFSIPState.SCHEDULED:
            raise MFSIPBookError("new SIP schedules may contain only SCHEDULED installments")
        if (item.account_id, item.folio_id, item.scheme_code, item.plan, item.option) != (
                first.account_id, first.folio_id, first.scheme_code, first.plan, first.option):
            raise MFSIPBookError("one SIP series must use a consistent account and scheme identity")
    plan_payload = [
        {"installment_id": item.installment_id, "scheduled_for": item.scheduled_for.isoformat(),
         "amount_inr": str(item.amount_inr)}
        for item in sorted(series.installments, key=lambda value: value.scheduled_for)
    ]
    plan_hash = hashlib.sha256(json.dumps(plan_payload, sort_keys=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()
    con.execute("BEGIN TRANSACTION")
    try:
        if con.execute("SELECT 1 FROM mf_paper_accounts WHERE account_id=?",
                        [first.account_id]).fetchone() is None:
            raise MFSIPBookError("paper account does not exist")
        old = con.execute("SELECT plan_sha256 FROM mf_paper_sip_plans WHERE plan_id=?",
                          [series.plan_id]).fetchone()
        if old is not None:
            if old[0] != plan_hash:
                raise MFSIPBookError("SIP plan ID was reused for a different schedule")
            con.execute("COMMIT")
            return {"plan_id": series.plan_id, "status": "already_created"}
        con.execute("""INSERT INTO mf_paper_sip_plans (
            plan_id, account_id, folio_id, scheme_code, plan, option,
            plan_sha256, created_at) VALUES (?,?,?,?,?,?,?,?)""",
            [series.plan_id, first.account_id, first.folio_id, first.scheme_code,
             first.plan, first.option, plan_hash, recorded_at])
        for item in series.installments:
            digest = _state_hash(item)
            payload = _snapshot_payload(item)
            values = [getattr(item, key) for key in (
                "installment_id", "plan_id", "account_id", "folio_id", "scheme_code",
                "plan", "option", "scheduled_for", "amount_inr", "state",
                "payment_initiated_at", "funds_available_at", "missed_at", "miss_reason",
                "nav_date", "nav_per_unit", "nav_published_at", "nav_available_at",
                "allotted_at", "units_allotted")]
            values[9] = item.state.value
            con.execute(f"""INSERT INTO mf_paper_sip_installments ({_COLUMNS})
                VALUES ({','.join('?' for _ in range(23))})""",
                [*values, digest, recorded_at, recorded_at])
            con.execute("""INSERT INTO mf_paper_sip_transitions (
                installment_id, sequence, from_state, to_state, effective_at,
                recorded_at, event_sha256, evidence_json)
                VALUES (?,1,NULL,'SCHEDULED',NULL,?,?,?)""",
                [item.installment_id, recorded_at, digest,
                 json.dumps(payload, sort_keys=True, separators=(",", ":"))])
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return {"plan_id": series.plan_id, "status": "created",
            "installment_count": len(series.installments)}


def advance_installment(con, following: MFSIPInstallment, *, now: datetime) -> dict[str, object]:
    """Atomically advance one installment; a missed installment cannot be retried."""
    recorded_at = _aware(now, "now")
    payload = _snapshot_payload(following)
    digest = _state_hash(following)
    con.execute("BEGIN TRANSACTION")
    try:
        row = con.execute(f"SELECT {_COLUMNS} FROM mf_paper_sip_installments WHERE installment_id=?",
                          [following.installment_id]).fetchone()
        if row is None:
            raise MFSIPBookError("SIP installment does not exist")
        previous = _from_row(row)
        previous_hash = row[20]
        if previous_hash == digest:
            con.execute("COMMIT")
            return {"installment_id": following.installment_id,
                    "state": following.state.value, "status": "already_applied"}
        try:
            validate_installment_transition(previous, following)
        except MFSIPError as exc:
            raise MFSIPBookError(str(exc)) from exc
        event_at = {
            MFSIPState.PAYMENT_INITIATED: following.payment_initiated_at,
            MFSIPState.FUNDS_AVAILABLE: following.funds_available_at,
            MFSIPState.NAV_PENDING: None,
            MFSIPState.MISSED: following.missed_at,
            MFSIPState.ALLOTTED: following.allotted_at,
        }[following.state]
        if event_at is not None and recorded_at < event_at:
            raise MFSIPBookError("installment transition recorded before event time")
        sequence = con.execute("""SELECT COALESCE(MAX(sequence),0)+1
            FROM mf_paper_sip_transitions WHERE installment_id=?""",
            [following.installment_id]).fetchone()[0]
        con.execute("""UPDATE mf_paper_sip_installments SET
            current_state=?, payment_initiated_at=?, funds_available_at=?, missed_at=?,
            miss_reason=?, nav_date=?, nav_per_unit=?, nav_published_at=?,
            nav_available_at=?, allotted_at=?, units_allotted=?, state_sha256=?, updated_at=?
            WHERE installment_id=?""",
            [following.state.value, following.payment_initiated_at,
             following.funds_available_at, following.missed_at, following.miss_reason,
             following.nav_date, following.nav_per_unit, following.nav_published_at,
             following.nav_available_at, following.allotted_at, following.units_allotted,
             digest, recorded_at, following.installment_id])
        con.execute("""INSERT INTO mf_paper_sip_transitions (
            installment_id, sequence, from_state, to_state, effective_at,
            recorded_at, event_sha256, evidence_json) VALUES (?,?,?,?,?,?,?,?)""",
            [following.installment_id, sequence, previous.state.value,
             following.state.value, event_at, recorded_at, digest,
             json.dumps(payload, sort_keys=True, separators=(",", ":"))])
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return {"installment_id": following.installment_id,
            "state": following.state.value, "status": "advanced"}


def installment_snapshot(con, *, installment_id: str) -> dict[str, object]:
    row = con.execute(f"SELECT {_COLUMNS} FROM mf_paper_sip_installments WHERE installment_id=?",
                      [installment_id]).fetchone()
    if row is None:
        raise MFSIPBookError("SIP installment does not exist")
    item = _from_row(row)
    return {"installment": item, "state_sha256": row[20],
            "transitions": con.execute("""SELECT sequence, from_state, to_state,
                effective_at, recorded_at, event_sha256, evidence_json
                FROM mf_paper_sip_transitions WHERE installment_id=? ORDER BY sequence""",
                [installment_id]).fetchall()}


__all__ = ["MFSIPBookError", "advance_installment", "create_sip_series", "installment_snapshot"]
