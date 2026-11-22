"""Apply sourced MF distributions and unit transitions to the durable paper book."""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_HALF_UP
from typing import Any

from ..forward_edge.data_permissions import DataPermission, DataUse
from ..ingest.mf_scheme_flows import as_known_on as flows_as_known_on
from ..ingest.mf_scheme_terms import as_known_on as terms_as_known_on
from ..kernel.trading_clock import IST
from ..providers.mf_scheme_flows import (
    SchemeEconomicFlow, SchemeFlowError, SchemeFlowKind,
)
from ..providers.mf_scheme_terms import ExitRestriction, SchemeCommercialTerms
from .mf_lot_book import MfPaperBookError

UNIT_QUANTUM = Decimal("0.000000000000000001")
PAISE = Decimal("0.01")


def _decimal(value: Decimal | str | int, label: str, *, positive: bool = True) -> Decimal:
    try:
        number = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise MfPaperBookError(f"{label} must be decimal") from exc
    if not number.is_finite() or number < 0 or (positive and number == 0):
        raise MfPaperBookError(f"{label} must be {'positive' if positive else 'non-negative'}")
    return number


def _permission_rows(permissions: tuple[DataPermission, ...]) -> list[tuple]:
    return sorted((
        item.source_id, item.use.value, item.status.value, item.effective_from,
        item.effective_through, item.evidence_ref, item.reviewer,
    ) for item in permissions)


def _paper_permission(
    permissions: tuple[DataPermission, ...], source_id: str, on: date,
) -> None:
    if not any(item.allows(source_id=source_id, use=DataUse.PAPER_SIMULATION, on=on)
               for item in permissions):
        raise MfPaperBookError(
            f"source {source_id} lacks effective PAPER_SIMULATION permission"
        )


def _require_terms(
    con,
    terms: SchemeCommercialTerms,
    *,
    permissions: tuple[DataPermission, ...],
    decision_at: datetime,
    effective_date: date,
) -> None:
    if terms.published_at > decision_at or terms.observed_at > decision_at:
        raise MfPaperBookError("commercial terms were not available at the decision time")
    if not terms.effective_from <= effective_date or (
        terms.effective_to is not None and effective_date > terms.effective_to
    ):
        raise MfPaperBookError("commercial terms are not effective on the flow date")
    try:
        terms.require_permissions(permissions)
    except ValueError as exc:
        raise MfPaperBookError(str(exc)) from exc
    _paper_permission(permissions, terms.source_id, decision_at.astimezone(IST).date())
    try:
        known = terms_as_known_on(
            con, terms.scheme_code, terms.plan, terms.option, terms.isin,
            decision_date=decision_at.astimezone(IST).date(), decision_at=decision_at,
        )
    except ValueError as exc:
        raise MfPaperBookError(str(exc)) from exc
    if known is None or known["record_id"] != terms.record_id:
        raise MfPaperBookError("commercial terms are not the resolved as-known version")


def _schedule_json(terms: SchemeCommercialTerms) -> str:
    if terms.exit_loads is None:
        raise MfPaperBookError("reinvestment lot exit-load schedule is unknown")
    return json.dumps([
        {"days_from_purchase": band.days_from_purchase,
         "rate_percent": str(band.rate_percent)}
        for band in terms.exit_loads
    ], sort_keys=True, separators=(",", ":"))


def _as_result(row: tuple, *, status: str) -> dict[str, Any]:
    names = (
        "account_id", "source_flow_id", "flow_kind", "scheme_code", "plan", "option",
        "isin", "target_scheme_code", "target_plan", "target_option", "target_isin",
        "effective_date", "units_before", "units_after", "cash_delta_inr", "nav_before",
        "nav_after", "value_before_inr", "value_after_inr", "total_return_percent",
        "reinvestment_lot_id", "terms_record_id", "event_sha256", "created_at",
    )
    result = dict(zip(names, row, strict=True))
    result["status"] = status
    return result


def apply_economic_flow(
    con,
    *,
    account_id: str,
    flow: SchemeEconomicFlow,
    decision_at: datetime,
    nav_before: Decimal | str | int,
    nav_after: Decimal | str | int,
    permissions: tuple[DataPermission, ...],
    reinvestment_lot_id: str | None = None,
    reinvestment_terms: SchemeCommercialTerms | None = None,
    reinvestment_lock_in_until: date | None = None,
    successor_terms: SchemeCommercialTerms | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Apply one persisted, as-known MF flow atomically to an account's open lots.

    IDCW payouts credit cash; reinvestments create a new lot with its own dated,
    permissioned terms; splits adjust open-lot units and purchase NAV; mergers transfer
    open lots only after successor terms resolve as known. Repeating the same event is
    idempotent, and reusing its ID for changed inputs is rejected.
    """
    if not account_id.strip():
        raise MfPaperBookError("account_id is required")
    if decision_at.tzinfo is None or decision_at.utcoffset() is None:
        raise MfPaperBookError("decision_at must be timezone-aware")
    decision_at = decision_at.astimezone(timezone.utc)
    event_at = now or datetime.now(timezone.utc)
    if event_at.tzinfo is None or event_at.utcoffset() is None:
        raise MfPaperBookError("flow application time must be timezone-aware")
    event_at = event_at.astimezone(timezone.utc)
    if event_at < decision_at:
        raise MfPaperBookError("flow application time precedes decision time")
    decision_date = decision_at.astimezone(IST).date()
    if (flow.published_at.astimezone(timezone.utc) > decision_at
            or flow.observed_at.astimezone(timezone.utc) > decision_at
            or flow.effective_date > decision_date):
        raise MfPaperBookError("economic flow is not available at the decision time")
    try:
        flow.require_permissions(permissions)
    except SchemeFlowError as exc:
        raise MfPaperBookError(str(exc)) from exc
    _paper_permission(permissions, flow.source_id, decision_date)

    if flow.kind == SchemeFlowKind.IDCW_REINVESTMENT:
        if not reinvestment_lot_id or reinvestment_terms is None:
            raise MfPaperBookError("reinvestment lot ID and effective terms are required")
        if reinvestment_terms.exit_restrictions is None:
            raise MfPaperBookError("reinvestment restrictions are unknown")
        is_elss = ExitRestriction.ELSS_LOCK_IN in reinvestment_terms.exit_restrictions
        if is_elss and reinvestment_lock_in_until is None:
            raise MfPaperBookError("ELSS reinvestment lot requires an explicit lock-in date")
        if reinvestment_lock_in_until is not None and reinvestment_lock_in_until < flow.effective_date:
            raise MfPaperBookError("reinvestment lock-in date precedes the flow date")
    elif reinvestment_lot_id is not None or reinvestment_terms is not None or reinvestment_lock_in_until is not None:
        raise MfPaperBookError("reinvestment terms are only valid for an IDCW reinvestment")

    if flow.kind == SchemeFlowKind.SCHEME_MERGER:
        if successor_terms is None:
            raise MfPaperBookError("successor commercial terms are required for a merger")
        if (successor_terms.scheme_code, successor_terms.isin) != (
            flow.successor_scheme_code, flow.successor_isin,
        ):
            raise MfPaperBookError("successor terms identity does not match the merger")
    elif successor_terms is not None:
        raise MfPaperBookError("successor terms are only valid for a scheme merger")

    nav0 = _decimal(nav_before, "NAV before")
    nav1 = _decimal(nav_after, "NAV after")
    reinvestment_terms_id = reinvestment_terms.record_id if reinvestment_terms else None
    successor_terms_id = successor_terms.record_id if successor_terms else None
    payload = {
        "account_id": account_id, "flow_id": flow.event_id, "flow_kind": flow.kind.value,
        "nav_before": nav0, "nav_after": nav1,
        "reinvestment_lot_id": reinvestment_lot_id,
        "reinvestment_terms_id": reinvestment_terms_id,
        "reinvestment_lock_in_until": reinvestment_lock_in_until,
        "successor_terms_id": successor_terms_id,
        "decision_at": decision_at, "permission_evidence": _permission_rows(permissions),
    }
    digest = hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")).hexdigest()

    con.execute("BEGIN TRANSACTION")
    try:
        old = con.execute("""SELECT account_id, source_flow_id, flow_kind, scheme_code,
            plan, option, isin, target_scheme_code, target_plan, target_option, target_isin,
            effective_date, units_before, units_after, cash_delta_inr, nav_before, nav_after,
            value_before_inr, value_after_inr, total_return_percent, reinvestment_lot_id,
            terms_record_id, event_sha256, created_at FROM mf_paper_flow_events
            WHERE account_id=? AND source_flow_id=?""",
            [account_id, flow.event_id]).fetchone()
        if old is not None:
            if old[-2] != digest:
                raise MfPaperBookError("source flow was already applied with different inputs")
            con.execute("COMMIT")
            return _as_result(old[:-2] + (digest, old[-1]), status="already_applied")

        source_facts = flows_as_known_on(
            con, flow.scheme_code, flow.isin, decision_at=decision_at,
        )
        if not any(item["event_id"] == flow.event_id for item in source_facts):
            raise MfPaperBookError("source flow is not persisted and known at decision time")

        account = con.execute(
            "SELECT cash_inr FROM mf_paper_accounts WHERE account_id=?", [account_id]
        ).fetchone()
        if account is None:
            raise MfPaperBookError("paper account does not exist")
        lots = con.execute("""SELECT lot_id, plan, option, allotted_on, lock_in_until,
            units_original, units_remaining, purchase_nav, folio_id FROM mf_paper_lots
            WHERE account_id=? AND scheme_code=? AND isin=? AND units_remaining>0
            ORDER BY allotted_on, lot_id""",
            [account_id, flow.scheme_code, flow.isin]).fetchall()
        if not lots:
            raise MfPaperBookError("no open lots for the economic-flow source scheme")
        if any(row[8] is None or not str(row[8]).strip() for row in lots):
            raise MfPaperBookError(
                "economic flow cannot mutate holdings with unreconciled folio ownership"
            )
        identities = {(row[1], row[2]) for row in lots}
        if len(identities) != 1:
            raise MfPaperBookError("source flow maps to ambiguous plan/option holdings")
        plan, option = next(iter(identities))
        units_before = sum((Decimal(row[6]) for row in lots), Decimal(0))
        if units_before <= 0:
            raise MfPaperBookError("source scheme has no positive units")

        target_scheme, target_plan, target_option, target_isin = (
            flow.scheme_code, plan, option, flow.isin,
        )
        terms_record_id = None
        cash_delta = Decimal(0)
        units_after = units_before
        extra_units = Decimal(0)
        reinvestment_allocations: list[tuple[str, str, Decimal]] = []
        reinvest_nav = None
        schedule_json = None
        schedule_hash = None
        if flow.kind == SchemeFlowKind.IDCW_PAYOUT:
            if flow.cash_per_unit is None:
                raise MfPaperBookError("IDCW payout amount is missing")
            cash_delta = units_before * Decimal(flow.cash_per_unit)
        elif flow.kind == SchemeFlowKind.IDCW_REINVESTMENT:
            assert reinvestment_terms is not None and flow.cash_per_unit is not None
            assert flow.reinvestment_nav is not None and reinvestment_lot_id is not None
            if (reinvestment_terms.scheme_code, reinvestment_terms.plan,
                reinvestment_terms.option, reinvestment_terms.isin) != (
                    flow.scheme_code, plan, option, flow.isin,
                ):
                raise MfPaperBookError("reinvestment terms identity does not match holdings")
            _require_terms(con, reinvestment_terms, permissions=permissions,
                           decision_at=decision_at, effective_date=flow.effective_date)
            schedule_json = _schedule_json(reinvestment_terms)
            schedule_hash = reinvestment_terms.source_sha256
            distribution = Decimal(0)
            reinvest_nav = Decimal(flow.reinvestment_nav)
            units_by_folio: dict[str, Decimal] = {}
            for row in lots:
                folio_id = str(row[8])
                units_by_folio[folio_id] = units_by_folio.get(folio_id, Decimal(0)) + Decimal(row[6])
            for folio_id, folio_units in sorted(units_by_folio.items()):
                folio_distribution = folio_units * Decimal(flow.cash_per_unit)
                folio_extra_units = (folio_distribution / reinvest_nav).quantize(
                    UNIT_QUANTUM, rounding=ROUND_DOWN
                )
                if folio_extra_units <= 0:
                    raise MfPaperBookError(
                        f"reinvestment distribution produces no units for folio {folio_id}"
                    )
                if len(units_by_folio) == 1:
                    folio_lot_id = reinvestment_lot_id
                else:
                    suffix = hashlib.sha256(folio_id.encode("utf-8")).hexdigest()[:16]
                    folio_lot_id = f"{reinvestment_lot_id}:{suffix}"
                reinvestment_allocations.append((folio_id, folio_lot_id, folio_extra_units))
                distribution += folio_distribution
                extra_units += folio_extra_units
            cash_delta = distribution - extra_units * reinvest_nav
            units_after += extra_units
            terms_record_id = reinvestment_terms.record_id
        elif flow.kind == SchemeFlowKind.UNIT_SPLIT:
            assert flow.unit_ratio is not None
            units_after = (units_before * Decimal(flow.unit_ratio)).quantize(
                UNIT_QUANTUM, rounding=ROUND_HALF_UP
            )
        elif flow.kind == SchemeFlowKind.SCHEME_MERGER:
            assert successor_terms is not None and flow.unit_ratio is not None
            _require_terms(con, successor_terms, permissions=permissions,
                           decision_at=decision_at, effective_date=flow.effective_date)
            units_after = (units_before * Decimal(flow.unit_ratio)).quantize(
                UNIT_QUANTUM, rounding=ROUND_HALF_UP
            )
            target_scheme, target_plan, target_option, target_isin = (
                successor_terms.scheme_code, successor_terms.plan,
                successor_terms.option, successor_terms.isin,
            )
            terms_record_id = successor_terms.record_id

        value_before = (units_before * nav0).quantize(PAISE, rounding=ROUND_HALF_UP)
        value_after = (units_after * nav1 + cash_delta).quantize(
            PAISE, rounding=ROUND_HALF_UP
        )
        if value_before <= 0:
            raise MfPaperBookError("position value before flow must be positive")
        total_return = ((value_after / value_before - Decimal(1)) * Decimal(100)).quantize(
            UNIT_QUANTUM, rounding=ROUND_HALF_UP
        )

        if flow.kind == SchemeFlowKind.IDCW_PAYOUT or (
            flow.kind == SchemeFlowKind.IDCW_REINVESTMENT and cash_delta != 0
        ):
            con.execute("UPDATE mf_paper_accounts SET cash_inr=cash_inr+? WHERE account_id=?",
                        [cash_delta, account_id])
        if flow.kind == SchemeFlowKind.IDCW_REINVESTMENT:
            allotted_on = flow.effective_date
            con.executemany("""INSERT INTO mf_paper_lots (
                account_id, lot_id, folio_id, scheme_code, plan, option, isin, allotted_on,
                lock_in_until, units_original, units_remaining, purchase_nav,
                exit_loads_json, exit_loads_sha256, subscription_event_id, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [[account_id, lot_id, folio_id, flow.scheme_code, plan, option,
                  flow.isin, allotted_on, reinvestment_lock_in_until, units,
                  units, reinvest_nav, schedule_json, schedule_hash,
                  (f"MF_FLOW:{flow.event_id}" if len(reinvestment_allocations) == 1
                   else f"MF_FLOW:{flow.event_id}:{folio_id}"), event_at]
                 for folio_id, lot_id, units in reinvestment_allocations])
        if flow.kind in (SchemeFlowKind.UNIT_SPLIT, SchemeFlowKind.SCHEME_MERGER):
            if flow.unit_ratio is None:
                raise MfPaperBookError("unit transition ratio is missing")
            ratio = Decimal(flow.unit_ratio)
            for row in lots:
                lot_id, _, _, _, _, original, remaining, purchase_nav, _folio_id = row
                adjusted_original = (Decimal(original) * ratio).quantize(
                    UNIT_QUANTUM, rounding=ROUND_HALF_UP
                )
                adjusted_remaining = (Decimal(remaining) * ratio).quantize(
                    UNIT_QUANTUM, rounding=ROUND_HALF_UP
                )
                adjusted_nav = (Decimal(purchase_nav) / ratio).quantize(
                    UNIT_QUANTUM, rounding=ROUND_HALF_UP
                )
                if adjusted_original <= 0 or adjusted_nav <= 0:
                    raise MfPaperBookError("unit transition exceeds paper-book precision")
                con.execute("""UPDATE mf_paper_lots SET scheme_code=?, plan=?, option=?,
                    isin=?, units_original=?, units_remaining=?, purchase_nav=?
                    WHERE account_id=? AND lot_id=?""",
                    [target_scheme, target_plan, target_option, target_isin,
                     adjusted_original, adjusted_remaining, adjusted_nav, account_id, lot_id])

        con.execute("""INSERT INTO mf_paper_flow_events (
            account_id, source_flow_id, flow_kind, scheme_code, plan, option, isin,
            target_scheme_code, target_plan, target_option, target_isin, effective_date,
            units_before, units_after, cash_delta_inr, nav_before, nav_after,
            value_before_inr, value_after_inr, total_return_percent,
            reinvestment_lot_id, terms_record_id, event_sha256, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [account_id, flow.event_id, flow.kind.value, flow.scheme_code, plan, option,
             flow.isin, target_scheme, target_plan, target_option, target_isin,
             flow.effective_date, units_before, units_after, cash_delta, nav0, nav1,
             value_before, value_after, total_return, reinvestment_lot_id,
             terms_record_id, digest, event_at])
        if (flow.kind in (SchemeFlowKind.IDCW_PAYOUT, SchemeFlowKind.IDCW_REINVESTMENT)
                and (flow.kind is SchemeFlowKind.IDCW_REINVESTMENT or cash_delta > 0)):
            from .mf_account_ledger import post_journal
            assert flow.cash_per_unit is not None
            distribution = units_before * Decimal(flow.cash_per_unit)
            journal_lines: list[tuple[str, Decimal | int, Decimal | int]] = []
            if flow.kind is SchemeFlowKind.IDCW_PAYOUT:
                if cash_delta:
                    journal_lines.append(("CASH", cash_delta, 0))
                journal_lines.append(("IDCW_INCOME", 0, distribution))
            else:
                assert reinvest_nav is not None
                invested = extra_units * reinvest_nav
                if invested:
                    journal_lines.append(("MF_INVESTMENTS", invested, 0))
                if cash_delta:
                    journal_lines.append(("CASH", cash_delta, 0))
                journal_lines.append(("IDCW_INCOME", 0, distribution))
            post_journal(
                con, account_id=account_id, transaction_id=f"scheme-flow:{flow.event_id}",
                effective_at=datetime.combine(flow.effective_date, datetime.min.time(),
                                               tzinfo=timezone.utc),
                recorded_at=event_at, entries=tuple(journal_lines),
                basis="OBSERVED_EVIDENCE", source_id=flow.source_id,
                source_ref=flow.source_ref, source_sha256=flow.source_sha256,
                evidence={"source_flow_id": flow.event_id,
                          "flow_kind": flow.kind.value,
                          "distribution_inr": str(distribution),
                          "units_before": str(units_before),
                          "units_after": str(units_after)},
            )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return {
        "account_id": account_id, "source_flow_id": flow.event_id,
        "flow_kind": flow.kind.value, "scheme_code": flow.scheme_code,
        "plan": plan, "option": option, "isin": flow.isin,
        "target_scheme_code": target_scheme, "target_plan": target_plan,
        "target_option": target_option, "target_isin": target_isin,
        "effective_date": flow.effective_date, "units_before": units_before,
        "units_after": units_after, "cash_delta_inr": cash_delta,
        "nav_before": nav0, "nav_after": nav1, "value_before_inr": value_before,
        "value_after_inr": value_after, "total_return_percent": total_return,
        "reinvestment_lot_id": reinvestment_lot_id,
        "terms_record_id": terms_record_id, "event_sha256": digest,
        "created_at": event_at, "status": "applied",
    }


__all__ = ["apply_economic_flow"]
