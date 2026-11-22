"""Durable paper persistence for SIP schedules and independent installments."""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from decimal import Decimal

from ..forward_edge.data_permissions import DataPermission, DataUse
from ..kernel.fund_transactions import SchemeTransactionCalendar, TransactionKind
from ..kernel.mf_sip import (
    MFSIPError, MFSIPInstallment, MFSIPSeries, MFSIPState,
    validate_installment_transition,
)
from ..kernel.trading_clock import IST


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


def advance_installment(
    con,
    following: MFSIPInstallment,
    *,
    now: datetime,
    payment_basis: str = "SIMULATED_ASSUMPTION",
    payment_source_id: str | None = None,
    payment_source_ref: str | None = None,
    payment_source_sha256: str | None = None,
    permissions: tuple[DataPermission, ...] = (),
    transition_evidence: dict[str, object] | None = None,
    _transaction_managed: bool = False,
) -> dict[str, object]:
    """Advance a SIP installment and journal its cleared payment atomically."""
    recorded_at = _aware(now, "now")
    payload = _snapshot_payload(following)
    digest = _state_hash(following)
    if following.state is MFSIPState.ALLOTTED and transition_evidence is None:
        raise MFSIPBookError("use apply_sip_allotment to post lots and allotment together")
    payment_evidence: dict[str, object] | None = None
    if following.state is MFSIPState.FUNDS_AVAILABLE:
        if payment_basis not in {"SIMULATED_ASSUMPTION", "OBSERVED_EVIDENCE"}:
            raise MFSIPBookError("unknown SIP payment evidence basis")
        if payment_basis == "OBSERVED_EVIDENCE":
            if not all((payment_source_id, payment_source_ref, payment_source_sha256)):
                raise MFSIPBookError("observed SIP payment requires source identity, reference and hash")
            assert payment_source_sha256 is not None
            if len(payment_source_sha256) != 64 or any(
                    char not in "0123456789abcdefABCDEF" for char in payment_source_sha256):
                raise MFSIPBookError("SIP payment source hash must be SHA-256")
            event_date = following.funds_available_at.astimezone(IST).date()  # type: ignore[union-attr]
            allowed = [permission for permission in permissions if permission.allows(
                source_id=payment_source_id or "", use=DataUse.PAPER_SIMULATION,
                on=event_date,
            )]
            if not allowed:
                raise MFSIPBookError("observed SIP payment lacks PAPER_SIMULATION permission")
            payment_evidence = {
                "basis": payment_basis, "source_id": payment_source_id,
                "source_ref": payment_source_ref,
                "source_sha256": payment_source_sha256.lower(),
                "permission_evidence": [{
                    "source_id": permission.source_id,
                    "evidence_ref": permission.evidence_ref,
                    "reviewer": permission.reviewer,
                    "effective_from": permission.effective_from.isoformat()
                    if permission.effective_from else None,
                    "effective_through": permission.effective_through.isoformat()
                    if permission.effective_through else None,
                } for permission in allowed],
            }
        else:
            if any((payment_source_id, payment_source_ref, payment_source_sha256)):
                raise MFSIPBookError("simulated SIP payment cannot claim source evidence")
            payment_evidence = {"basis": payment_basis}
    elif any((payment_source_id, payment_source_ref, payment_source_sha256)):
        raise MFSIPBookError("payment provenance is only valid for funds availability")
    transition_payload: dict[str, object] = payload
    if payment_evidence is not None or transition_evidence is not None:
        transition_payload = {"installment": payload}
        if payment_evidence is not None:
            transition_payload["payment_evidence"] = payment_evidence
        if transition_evidence is not None:
            transition_payload["transition_evidence"] = transition_evidence
    if not _transaction_managed:
        con.execute("BEGIN TRANSACTION")
    try:
        row = con.execute(f"SELECT {_COLUMNS} FROM mf_paper_sip_installments WHERE installment_id=?",
                          [following.installment_id]).fetchone()
        if row is None:
            raise MFSIPBookError("SIP installment does not exist")
        previous = _from_row(row)
        previous_hash = row[20]
        if previous_hash == digest:
            latest = con.execute("""SELECT evidence_json FROM mf_paper_sip_transitions
                WHERE installment_id=? ORDER BY sequence DESC LIMIT 1""",
                [following.installment_id]).fetchone()
            if latest is None or json.loads(latest[0]) != transition_payload:
                raise MFSIPBookError("SIP retry changed payment or allotment evidence")
            if not _transaction_managed:
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
        if following.state is MFSIPState.FUNDS_AVAILABLE:
            assert following.funds_available_at is not None
            account = con.execute("SELECT cash_inr FROM mf_paper_accounts WHERE account_id=?",
                                  [following.account_id]).fetchone()
            if account is None:
                raise MFSIPBookError("paper account does not exist")
            available_cash = Decimal(account[0])
            if following.amount_inr > available_cash:
                raise MFSIPBookError("SIP payment exceeds available paper cash")
            con.execute("UPDATE mf_paper_accounts SET cash_inr=? WHERE account_id=?",
                        [available_cash - following.amount_inr, following.account_id])
            from .mf_account_ledger import post_journal
            post_journal(
                con, account_id=following.account_id,
                transaction_id=f"sip-payment:{following.installment_id}",
                effective_at=following.funds_available_at,
                recorded_at=recorded_at,
                entries=(("SIP_PAYMENT_PENDING", following.amount_inr, 0),
                         ("CASH", 0, following.amount_inr)),
                basis=payment_basis, source_id=payment_source_id,
                source_ref=payment_source_ref, source_sha256=payment_source_sha256,
                evidence={"installment_id": following.installment_id,
                          "plan_id": following.plan_id,
                          "amount_inr": str(following.amount_inr),
                          "payment_evidence": payment_evidence},
            )
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
             json.dumps(transition_payload, sort_keys=True, separators=(",", ":"))])
        if not _transaction_managed:
            con.execute("COMMIT")
    except Exception:
        if not _transaction_managed:
            con.execute("ROLLBACK")
        raise
    return {"installment_id": following.installment_id,
            "state": following.state.value, "status": "advanced"}


def apply_sip_allotment(
    con,
    *,
    installment_id: str,
    order_id: str,
    nav_per_unit: Decimal | str | int,
    nav_observation_id: str,
    scheme_calendar: SchemeTransactionCalendar,
    terms,
    permissions: tuple[DataPermission, ...],
    decision_at: datetime,
    allotted_at: datetime,
    now: datetime,
    lock_in_until: date | None = None,
) -> dict[str, object]:
    """Atomically consume a reserved SIP payment into an observed NAV lot.

    Acceptance time is read from the durable MF order book, not caller input. The
    eligible NAV date is derived from the later of accepted-order time and cleared
    payment time using the source-versioned scheme cutoff/calendar. The NAV must be the
    exact persisted observation for that date available at the decision time.
    The account journal, FIFO lot, subscription event and ALLOTTED state commit or
    roll back together; replaying the same event is idempotent.
    """
    from ..ingest.mfnav import as_known_on as mf_nav_as_known_on
    from ..kernel.mf_order import MFOrderKind, MFOrderState
    from .mf_lot_book import subscribe

    decision_time = _aware(decision_at, "decision_at")
    allotment_time = _aware(allotted_at, "allotted_at")
    recorded_at = _aware(now, "now")
    try:
        nav = Decimal(str(nav_per_unit))
    except Exception as exc:
        raise MFSIPBookError("NAV must be a positive finite decimal") from exc
    if not nav.is_finite() or nav <= 0:
        raise MFSIPBookError("NAV must be a positive finite decimal")
    if not nav_observation_id.strip():
        raise MFSIPBookError("SIP allotment requires a NAV observation ID")
    if not order_id.strip():
        raise MFSIPBookError("SIP allotment requires its durable accepted order ID")
    if recorded_at < allotment_time or allotment_time < decision_time:
        raise MFSIPBookError("SIP allotment clocks are out of order")

    con.execute("BEGIN TRANSACTION")
    try:
        row = con.execute(
            f"SELECT {_COLUMNS} FROM mf_paper_sip_installments WHERE installment_id=?",
            [installment_id],
        ).fetchone()
        if row is None:
            raise MFSIPBookError("SIP installment does not exist")
        installment = _from_row(row)
        if installment.state not in {
            MFSIPState.FUNDS_AVAILABLE, MFSIPState.NAV_PENDING, MFSIPState.ALLOTTED,
        }:
            raise MFSIPBookError("SIP allotment requires cleared installment funds")
        if installment.funds_available_at is None or decision_time < installment.funds_available_at:
            raise MFSIPBookError("SIP decision precedes cleared payment funds")
        order_row = con.execute("""SELECT account_id, folio_id, scheme_code, plan,
            option, transaction_type, requested_at, amount_inr, units, current_state
            FROM mf_paper_orders WHERE order_id=?""", [order_id]).fetchone()
        if order_row is None:
            raise MFSIPBookError("SIP paper order does not exist")
        (order_account, order_folio, order_scheme, order_plan, order_option,
         order_kind, order_requested_at, order_amount, order_units, order_state) = order_row
        if (order_account, order_folio, order_scheme, order_plan, order_option) != (
            installment.account_id, installment.folio_id, installment.scheme_code,
            installment.plan, installment.option,
        ) or (order_kind != MFOrderKind.PURCHASE.value or order_amount is None
             or Decimal(order_amount) != installment.amount_inr or order_units is not None):
            raise MFSIPBookError("accepted MF order does not match the SIP installment")
        if order_state != MFOrderState.ACCEPTED.value:
            raise MFSIPBookError("SIP allotment requires a durably accepted MF order")
        accepted_receipt = con.execute("""SELECT effective_at, received_at, event_sha256
            FROM mf_paper_order_transitions WHERE order_id=? AND state='ACCEPTED'
            ORDER BY sequence DESC LIMIT 1""", [order_id]).fetchone()
        if accepted_receipt is None:
            raise MFSIPBookError("accepted MF order has no persisted acceptance receipt")
        accepted_time = _aware(accepted_receipt[0], "accepted order effective time")
        accepted_received_at = _aware(accepted_receipt[1], "accepted order receipt time")
        if decision_time < accepted_received_at:
            raise MFSIPBookError("SIP decision precedes receipt of the accepted order")
        if order_requested_at > accepted_time:
            raise MFSIPBookError("accepted order effective time precedes its request")
        if (installment.payment_initiated_at is not None
                and order_requested_at < installment.payment_initiated_at):
            raise MFSIPBookError("SIP order request precedes payment initiation")
        if (terms.scheme_code, terms.plan, terms.option) != (
            installment.scheme_code, installment.plan, installment.option,
        ):
            raise MFSIPBookError("SIP commercial terms do not match the scheduled scheme")

        transaction_time = max(accepted_time, installment.funds_available_at)
        try:
            rule = scheme_calendar.rule_as_known(
                installment.scheme_code, TransactionKind.PURCHASE,
                transaction_at=transaction_time,
            )
            eligible_nav_date = scheme_calendar.assign_nav_date(
                installment.scheme_code, TransactionKind.PURCHASE,
                received_at=transaction_time,
            )
            scheme_business_calendar = scheme_calendar.calendars[installment.scheme_code]
        except (KeyError, ValueError) as exc:
            raise MFSIPBookError(f"cannot assign SIP eligible NAV date: {exc}") from exc
        if scheme_business_calendar.recorded_at > transaction_time:
            raise MFSIPBookError("scheme business calendar was not known at transaction time")
        observation = mf_nav_as_known_on(
            con, installment.scheme_code, eligible_nav_date, known_at=decision_time,
        )
        if (observation is None or observation["observation_id"] != nav_observation_id
                or Decimal(str(observation["nav"])) != nav):
            raise MFSIPBookError(
                "SIP allotment NAV must match the exact as-known observation for its eligible NAV date"
            )
        available_at = observation["available_at"]
        published_at = observation["source_published_at"]
        if available_at > allotment_time or (published_at is not None
                                              and published_at > available_at):
            raise MFSIPBookError("SIP allotment NAV clocks are not causally valid")
        if not any(permission.allows(
                source_id="AMFI", use=DataUse.PAPER_SIMULATION,
                on=decision_time.astimezone(IST).date()) for permission in permissions):
            raise MFSIPBookError("AMFI SIP NAV lacks effective PAPER_SIMULATION permission")

        units = installment.amount_inr / nav
        if (units * nav).quantize(Decimal("0.01")) != installment.amount_inr.quantize(Decimal("0.01")):
            raise MFSIPBookError("SIP units times NAV do not reconcile to the installment")
        following = MFSIPInstallment(
            installment_id=installment.installment_id,
            plan_id=installment.plan_id,
            account_id=installment.account_id,
            folio_id=installment.folio_id,
            scheme_code=installment.scheme_code,
            plan=installment.plan,
            option=installment.option,
            scheduled_for=installment.scheduled_for,
            amount_inr=installment.amount_inr,
            state=MFSIPState.ALLOTTED,
            payment_initiated_at=installment.payment_initiated_at,
            funds_available_at=installment.funds_available_at,
            nav_date=eligible_nav_date,
            nav_per_unit=nav,
            nav_published_at=published_at,
            nav_available_at=available_at,
            allotted_at=allotment_time,
            units_allotted=units,
        )
        evidence = {
            "accepted_order_id": order_id,
            "accepted_order_receipt_sha256": accepted_receipt[2],
            "accepted_order_effective_at": accepted_time.isoformat(),
            "accepted_order_received_at": accepted_received_at.isoformat(),
            "nav_observation_id": nav_observation_id,
            "nav_row_sha256": observation["row_sha256"],
            "nav_available_at": available_at.isoformat(),
            "funds_available_at": installment.funds_available_at.isoformat(),
            "eligible_nav_date": eligible_nav_date.isoformat(),
            "transaction_rule_source_ref": rule.source_ref,
            "transaction_rule_source_sha256": rule.source_sha256,
            "transaction_rule_effective_from": rule.effective_from.isoformat(),
            "transaction_rule_effective_to": (rule.effective_to.isoformat()
                                               if rule.effective_to else None),
            "transaction_rule_published_at": rule.published_at.isoformat(),
            "transaction_rule_cutoff_local": rule.cutoff_local.isoformat(),
            "transaction_rule_lags": {
                "before_cutoff": rule.before_cutoff_lag,
                "after_cutoff": rule.after_cutoff_lag,
                "closed_day": rule.closed_day_lag,
            },
            "scheme_calendar_source_ref": scheme_business_calendar.source_ref,
            "scheme_calendar_source_sha256": scheme_business_calendar.source_sha256,
            "scheme_calendar_sha256": scheme_business_calendar.sha256(),
            "scheme_calendar_recorded_at": scheme_business_calendar.recorded_at.isoformat(),
            "terms_record_id": terms.record_id,
        }
        subscription = subscribe(
            con, account_id=installment.account_id,
            event_id=f"sip:{installment.installment_id}",
            lot_id=f"sip-lot:{installment.installment_id}",
            scheme_code=installment.scheme_code, plan=installment.plan,
            option=installment.option, isin=terms.isin, allotted_on=eligible_nav_date,
            lock_in_until=lock_in_until, investment_inr=installment.amount_inr,
            nav_per_unit=nav, exit_loads=terms.exit_loads,
            exit_loads_sha256=terms.source_sha256 if terms.exit_loads is not None else None,
            terms=terms, permissions=permissions, decision_at=decision_time,
            now=allotment_time, funding_account_code="SIP_PAYMENT_PENDING",
            _transaction_managed=True,
        )
        transition = advance_installment(
            con, following, now=recorded_at, transition_evidence=evidence,
            _transaction_managed=True,
        )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return {**subscription, **transition, "nav_observation_id": nav_observation_id,
            "units": units, "nav_per_unit": nav}


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


__all__ = ["MFSIPBookError", "advance_installment", "apply_sip_allotment",
           "create_sip_series", "installment_snapshot"]
