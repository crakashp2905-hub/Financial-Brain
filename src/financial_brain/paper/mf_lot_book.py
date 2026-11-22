"""Durable paper-book accounting for MF subscription lots and redemptions.

Each subscription creates an immutable lot with its own source-pinned exit-load
schedule. Redemptions allocate FIFO across those lots, reconcile fees into account
cash, and are idempotent by caller-supplied event ID.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from ..forward_edge.data_permissions import DataPermission, DataUse
from ..ingest.mf_scheme_terms import as_known_on as mf_terms_as_known_on
from ..kernel.trading_clock import IST
from ..providers.mf_scheme_terms import (
    ExitLoadBand,
    ExitRestriction,
    SchemeCommercialTerms,
)
from .mf_exit_loads import PurchaseLot, calculate_exit_loads
from .mf_redemption_constraints import resolve_redemption_eligibility


class MfPaperBookError(ValueError):
    """The requested paper-book event cannot be reconciled safely."""


def _decimal(value: Decimal | str | int, name: str, *, positive: bool = True) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise MfPaperBookError(f"{name} must be decimal") from exc
    if not result.is_finite() or result < 0 or (positive and result == 0):
        qualifier = "positive" if positive else "non-negative"
        raise MfPaperBookError(f"{name} must be {qualifier}")
    return result


def _aware(value: datetime | None) -> datetime:
    result = value or datetime.now(timezone.utc)
    if result.tzinfo is None or result.utcoffset() is None:
        raise MfPaperBookError("event timestamp must be timezone-aware")
    return result.astimezone(timezone.utc)


def _schedule_json(schedule: tuple[ExitLoadBand, ...] | None) -> str | None:
    if schedule is None:
        return None
    return json.dumps(
        [
            {
                "days_from_purchase": band.days_from_purchase,
                "rate_percent": str(band.rate_percent),
            }
            for band in schedule
        ],
        sort_keys=True,
        separators=(",", ":"),
    )


def _schedule_from_json(raw: str | None) -> tuple[ExitLoadBand, ...] | None:
    if raw is None:
        return None
    values = json.loads(raw)
    return tuple(
        ExitLoadBand(item["days_from_purchase"], item["rate_percent"]) for item in values
    )


def _sha256(value: str, label: str) -> str:
    if not re.fullmatch(r"[0-9a-fA-F]{64}", value):
        raise MfPaperBookError(f"{label} must be a 64-character SHA-256 digest")
    return value.lower()


def _event_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _permission_evidence(permissions: tuple[DataPermission, ...]) -> list[tuple]:
    return sorted(
        (
            permission.source_id,
            permission.use.value,
            permission.status.value,
            permission.effective_from,
            permission.effective_through,
            permission.evidence_ref,
            permission.reviewer,
        )
        for permission in permissions
    )


def _require_paper_terms(
    con,
    terms: SchemeCommercialTerms,
    permissions: tuple[DataPermission, ...],
    decision_at: datetime,
) -> None:
    if terms.published_at > decision_at or terms.observed_at > decision_at:
        raise MfPaperBookError("commercial terms were not available at the decision time")
    try:
        terms.require_permissions(permissions)
    except ValueError as exc:
        raise MfPaperBookError(str(exc)) from exc
    permission_date = decision_at.astimezone(IST).date()
    if not any(
        permission.allows(
            source_id=terms.source_id,
            use=DataUse.PAPER_SIMULATION,
            on=permission_date,
        )
        for permission in permissions
    ):
        raise MfPaperBookError("source lacks effective PAPER_SIMULATION permission")
    try:
        known = mf_terms_as_known_on(
            con,
            terms.scheme_code,
            terms.plan,
            terms.option,
            terms.isin,
            decision_date=permission_date,
            decision_at=decision_at,
        )
    except ValueError as exc:
        raise MfPaperBookError(str(exc)) from exc
    if known is None or known["record_id"] != terms.record_id:
        raise MfPaperBookError("supplied commercial terms are not the resolved as-known version")


def create_account(
    con,
    account_id: str,
    opening_cash_inr: Decimal | str | int,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Create an account once; repeating the same opening balance is a no-op."""
    if not account_id.strip():
        raise MfPaperBookError("account_id is required")
    cash = _decimal(opening_cash_inr, "opening cash", positive=False)
    created = _aware(now)
    con.execute("BEGIN TRANSACTION")
    try:
        old = con.execute(
            "SELECT cash_inr FROM mf_paper_accounts WHERE account_id = ?", [account_id]
        ).fetchone()
        if old is not None:
            if Decimal(old[0]) != cash:
                raise MfPaperBookError(
                    "account already exists with a different opening balance"
                )
            con.execute("COMMIT")
            return {"account_id": account_id, "cash_inr": cash, "status": "exists"}
        con.execute(
            "INSERT INTO mf_paper_accounts VALUES (?, ?, ?)", [account_id, cash, created]
        )
        from .mf_account_ledger import post_journal
        if cash:
            post_journal(
                con, account_id=account_id, transaction_id="opening-balance",
                effective_at=created, recorded_at=created,
                entries=(("CASH", cash, 0), ("OWNER_EQUITY", 0, cash)),
                evidence={"kind": "PAPER_ACCOUNT_OPENING"},
            )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return {"account_id": account_id, "cash_inr": cash, "status": "created"}


def _existing_event(con, account_id: str, event_id: str, digest: str):
    row = con.execute(
        """SELECT kind, scheme_code, plan, option, isin, event_date, nav_per_unit,
        units, gross_inr, exit_load_inr, net_inr, event_sha256
        FROM mf_paper_events WHERE account_id = ? AND event_id = ?""",
        [account_id, event_id],
    ).fetchone()
    if row is None:
        return None
    if row[-1] != digest:
        raise MfPaperBookError("event_id was already used for different event content")
    keys = (
        "kind",
        "scheme_code",
        "plan",
        "option",
        "isin",
        "event_date",
        "nav_per_unit",
        "units",
        "gross_inr",
        "exit_load_inr",
        "net_inr",
    )
    result = dict(zip(keys, row[:-1], strict=True))
    result.update(account_id=account_id, event_id=event_id, status="already_applied")
    return result


def subscribe(
    con,
    *,
    account_id: str,
    event_id: str,
    lot_id: str,
    scheme_code: str,
    plan: str,
    option: str,
    isin: str,
    allotted_on: date,
    lock_in_until: date | None = None,
    investment_inr: Decimal | str | int,
    nav_per_unit: Decimal | str | int,
    exit_loads: tuple[ExitLoadBand, ...] | None,
    exit_loads_sha256: str | None,
    terms: SchemeCommercialTerms,
    permissions: tuple[DataPermission, ...],
    decision_at: datetime,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Debit cash and create a subscription lot using the explicitly supplied NAV."""
    for label, value in (
        ("account_id", account_id),
        ("event_id", event_id),
        ("lot_id", lot_id),
        ("scheme_code", scheme_code),
        ("plan", plan),
        ("option", option),
        ("isin", isin),
    ):
        if not value.strip():
            raise MfPaperBookError(f"{label} is required")
    if lock_in_until is not None and lock_in_until < allotted_on:
        raise MfPaperBookError("lock_in_until precedes lot allotment")
    amount, nav = _decimal(investment_inr, "investment"), _decimal(nav_per_unit, "NAV")
    decision_at = _aware(decision_at)
    event_at = _aware(now)
    if event_at < decision_at:
        raise MfPaperBookError("subscription event timestamp precedes the decision time")
    if (terms.scheme_code, terms.plan, terms.option, terms.isin) != (
        scheme_code,
        plan,
        option,
        isin,
    ):
        raise MfPaperBookError(
            "commercial terms identity does not match the subscribed lot"
        )
    if not terms.effective_from <= allotted_on or (
        terms.effective_to is not None and allotted_on > terms.effective_to
    ):
        raise MfPaperBookError("commercial terms are not effective on the allotment date")
    if terms.exit_loads != exit_loads:
        raise MfPaperBookError("lot exit-load schedule differs from commercial terms")
    _require_paper_terms(con, terms, permissions, decision_at)
    schedule = _schedule_json(exit_loads)
    if exit_loads is None:
        if exit_loads_sha256 is not None:
            raise MfPaperBookError("unknown exit-load schedule cannot have a schedule hash")
        schedule_hash = None
    else:
        if exit_loads_sha256 is None:
            raise MfPaperBookError("known exit-load schedule requires its source hash")
        schedule_hash = _sha256(exit_loads_sha256, "exit-load source hash")
        if schedule_hash != terms.source_sha256:
            raise MfPaperBookError("exit-load hash differs from the source terms hash")
    units = amount / nav
    payload = {
        "kind": "SUBSCRIBE",
        "account_id": account_id,
        "event_id": event_id,
        "lot_id": lot_id,
        "scheme_code": scheme_code,
        "plan": plan,
        "option": option,
        "isin": isin,
        "allotted_on": allotted_on,
        "lock_in_until": lock_in_until,
        "investment_inr": amount,
        "nav_per_unit": nav,
        "exit_loads_json": schedule,
        "exit_loads_sha256": schedule_hash,
        "terms_record_id": terms.record_id,
        "decision_at": decision_at,
        "permission_evidence": _permission_evidence(permissions),
    }
    digest = _event_hash(payload)

    con.execute("BEGIN TRANSACTION")
    try:
        existing = _existing_event(con, account_id, event_id, digest)
        if existing is not None:
            con.execute("COMMIT")
            return existing
        account = con.execute(
            "SELECT cash_inr FROM mf_paper_accounts WHERE account_id = ?", [account_id]
        ).fetchone()
        if account is None:
            raise MfPaperBookError("paper account does not exist")
        has_prior_subscription = con.execute(
            """SELECT 1 FROM mf_paper_events WHERE account_id=? AND scheme_code=?
            AND plan=? AND option=? AND isin=? AND kind='SUBSCRIBE' LIMIT 1""",
            [account_id, scheme_code, plan, option, isin],
        ).fetchone() is not None
        minimum = (
            terms.minimum_additional_inr
            if has_prior_subscription else terms.minimum_initial_inr
        )
        if minimum is None:
            raise MfPaperBookError("sourced subscription minimum is unknown")
        if amount < Decimal(str(minimum)):
            kind = "additional" if has_prior_subscription else "initial"
            raise MfPaperBookError(
                f"subscription is below the sourced {kind} minimum of INR {minimum}"
            )
        cash = Decimal(account[0])
        if amount > cash:
            raise MfPaperBookError("subscription exceeds available paper cash")
        con.execute(
            """INSERT INTO mf_paper_lots (
            account_id, lot_id, scheme_code, plan, option, isin, allotted_on, lock_in_until,
            units_original, units_remaining, purchase_nav, exit_loads_json, exit_loads_sha256,
            subscription_event_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                account_id,
                lot_id,
                scheme_code,
                plan,
                option,
                isin,
                allotted_on,
                lock_in_until,
                units,
                units,
                nav,
                schedule,
                schedule_hash,
                event_id,
                event_at,
            ],
        )
        con.execute(
            "UPDATE mf_paper_accounts SET cash_inr = ? WHERE account_id = ?",
            [cash - amount, account_id],
        )
        con.execute(
            """INSERT INTO mf_paper_events (
            account_id, event_id, kind, scheme_code, plan, option, isin, event_date,
            nav_per_unit, units, gross_inr, exit_load_inr, net_inr, event_sha256, created_at)
            VALUES (
            ?, ?, 'SUBSCRIBE', ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)""",
            [
                account_id,
                event_id,
                scheme_code,
                plan,
                option,
                isin,
                allotted_on,
                nav,
                units,
                amount,
                amount,
                digest,
                event_at,
            ],
        )
        from .mf_account_ledger import post_journal
        post_journal(
            con, account_id=account_id, transaction_id=f"subscription:{event_id}",
            effective_at=event_at, recorded_at=event_at,
            entries=(("MF_INVESTMENTS", amount, 0), ("CASH", 0, amount)),
            evidence={"event_id": event_id, "lot_id": lot_id,
                      "nav_per_unit": str(nav), "units": str(units),
                      "terms_record_id": terms.record_id},
        )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return {
        "account_id": account_id,
        "event_id": event_id,
        "kind": "SUBSCRIBE",
        "scheme_code": scheme_code,
        "plan": plan,
        "option": option,
        "isin": isin,
        "event_date": allotted_on,
        "nav_per_unit": nav,
        "units": units,
        "gross_inr": amount,
        "exit_load_inr": Decimal(0),
        "net_inr": amount,
        "status": "applied",
    }


def redeem(
    con,
    *,
    account_id: str,
    event_id: str,
    scheme_code: str,
    plan: str,
    option: str,
    isin: str,
    redemption_date: date,
    redemption_units: Decimal | str | int,
    nav_per_unit: Decimal | str | int,
    terms: SchemeCommercialTerms,
    permissions: tuple[DataPermission, ...],
    decision_at: datetime,
    redemption_window_open: bool | None = None,
    gating_remaining_units: Decimal | str | int | None = None,
    segregated_portfolio_units: Decimal | str | int | None = None,
    settlement_id: str | None = None,
    nav_observation_id: str | None = None,
    payout_due_date: date | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Apply FIFO lots, optionally keeping proceeds pending settlement.

    Without ``settlement_id`` this retains the legacy immediate-credit behavior. With
    one, the NAV must resolve to the exact as-known ingestion observation, the request
    must be pending, units are reduced once, and proceeds remain unavailable until an
    explicit cash-availability event.
    """
    for label, value in (
        ("account_id", account_id),
        ("event_id", event_id),
        ("scheme_code", scheme_code),
        ("plan", plan),
        ("option", option),
        ("isin", isin),
    ):
        if not value.strip():
            raise MfPaperBookError(f"{label} is required")
    units = _decimal(redemption_units, "redemption units")
    nav = _decimal(nav_per_unit, "NAV")
    minimum_redemption = terms.minimum_redemption_inr
    if minimum_redemption is None:
        raise MfPaperBookError("sourced redemption minimum is unknown")
    if units * nav < Decimal(str(minimum_redemption)):
        raise MfPaperBookError(
            "gross redemption value is below the sourced minimum of "
            f"INR {minimum_redemption}"
        )
    event_at = _aware(now)
    decision_at = _aware(decision_at)
    if (terms.scheme_code, terms.plan, terms.option, terms.isin) != (
        scheme_code,
        plan,
        option,
        isin,
    ):
        raise MfPaperBookError("commercial terms identity does not match the redeemed lots")
    if not terms.effective_from <= redemption_date or (
        terms.effective_to is not None and redemption_date > terms.effective_to
    ):
        raise MfPaperBookError("commercial terms are not effective on the redemption date")
    if event_at < decision_at:
        raise MfPaperBookError("redemption event timestamp precedes the decision time")
    _require_paper_terms(con, terms, permissions, decision_at)
    nav_observation = None
    if settlement_id is not None:
        if not settlement_id.strip() or not nav_observation_id or payout_due_date is None:
            raise MfPaperBookError(
                "settled redemption requires settlement ID, NAV observation ID and payout due date"
            )
        if payout_due_date < redemption_date:
            raise MfPaperBookError("payout due date precedes the eligible NAV date")
        from ..ingest.mfnav import as_known_on as mf_nav_as_known_on

        nav_observation = mf_nav_as_known_on(
            con, scheme_code, redemption_date, known_at=decision_at,
        )
        if (nav_observation is None
                or nav_observation["observation_id"] != nav_observation_id
                or Decimal(str(nav_observation["nav"])) != nav):
            raise MfPaperBookError(
                "settled redemption NAV must match the persisted as-known observation"
            )
        source_published_at = nav_observation["source_published_at"]
        if source_published_at is not None and source_published_at > decision_at:
            raise MfPaperBookError("NAV source publication was after value confirmation")
    elif nav_observation_id is not None or payout_due_date is not None:
        raise MfPaperBookError("NAV observation and payout date require a settlement ID")
    payload = {
        "kind": "REDEEM",
        "account_id": account_id,
        "event_id": event_id,
        "scheme_code": scheme_code,
        "plan": plan,
        "option": option,
        "isin": isin,
        "redemption_date": redemption_date,
        "redemption_units": units,
        "nav_per_unit": nav,
        "terms_record_id": terms.record_id,
        "decision_at": decision_at,
        "redemption_window_open": redemption_window_open,
        "gating_remaining_units": gating_remaining_units,
        "segregated_portfolio_units": segregated_portfolio_units,
        "settlement_id": settlement_id,
        "nav_observation_id": nav_observation_id,
        "payout_due_date": payout_due_date,
        "permission_evidence": _permission_evidence(permissions),
    }
    digest = _event_hash(payload)
    con.execute("BEGIN TRANSACTION")
    try:
        existing = _existing_event(con, account_id, event_id, digest)
        if existing is not None:
            con.execute("COMMIT")
            return existing
        account = con.execute(
            "SELECT cash_inr FROM mf_paper_accounts WHERE account_id = ?", [account_id]
        ).fetchone()
        if account is None:
            raise MfPaperBookError("paper account does not exist")
        settlement = None
        if settlement_id is not None:
            settlement = con.execute("""SELECT scheme_code, plan, option, isin,
                requested_units, requested_on, requested_at, state
                FROM mf_paper_redemption_settlements
                WHERE account_id=? AND settlement_id=?""",
                [account_id, settlement_id]).fetchone()
            if settlement is None:
                raise MfPaperBookError("redemption settlement request does not exist")
            if settlement[7] != "REDEMPTION_PENDING":
                raise MfPaperBookError("redemption settlement is not pending value confirmation")
            if tuple(settlement[:4]) != (scheme_code, plan, option, isin):
                raise MfPaperBookError("redemption settlement identity does not match request")
            if Decimal(settlement[4]) != units:
                raise MfPaperBookError("redemption units differ from the pending request")
            if settlement[5] > redemption_date or settlement[6] > decision_at:
                raise MfPaperBookError("redemption valuation predates its request")
        rows = con.execute(
            """SELECT lot_id, allotted_on, units_remaining, exit_loads_json, lock_in_until,
                purchase_nav
            FROM mf_paper_lots WHERE account_id = ? AND scheme_code = ? AND plan = ?
            AND option = ? AND isin = ? AND units_remaining > 0
            ORDER BY allotted_on, lot_id""",
            [account_id, scheme_code, plan, option, isin],
        ).fetchall()
        if not rows:
            raise MfPaperBookError("no open purchase lots for this scheme")
        lots = tuple(
            PurchaseLot(row[0], row[1], Decimal(row[2]), _schedule_from_json(row[3]))
            for row in rows
        )
        restrictions = terms.exit_restrictions
        if restrictions is None:
            raise MfPaperBookError("commercial redemption restrictions are unknown")
        available_units = sum((Decimal(lot.units) for lot in lots), Decimal(0))
        global_eligibility = resolve_redemption_eligibility(
            as_of=redemption_date,
            requested_units=units,
            lot_units=available_units,
            restrictions=tuple(
                item for item in restrictions if item != ExitRestriction.ELSS_LOCK_IN
            ),
            redemption_window_open=redemption_window_open,
            gating_remaining_units=gating_remaining_units,
            segregated_portfolio_units=segregated_portfolio_units,
        )
        if not global_eligibility.eligible:
            raise MfPaperBookError(",".join(global_eligibility.reasons))
        remaining_to_check = units
        gate_remaining = (
            Decimal(str(gating_remaining_units))
            if gating_remaining_units is not None
            else None
        )
        segregated_remaining = (
            Decimal(str(segregated_portfolio_units))
            if segregated_portfolio_units is not None
            else None
        )
        for lot, row in zip(lots, rows, strict=True):
            if remaining_to_check <= 0:
                break
            lot_units = Decimal(lot.units)
            portion = min(remaining_to_check, lot_units)
            lot_eligibility = resolve_redemption_eligibility(
                as_of=redemption_date,
                requested_units=portion,
                lot_units=lot_units,
                restrictions=restrictions,
                lock_in_until=row[4],
                redemption_window_open=redemption_window_open,
                gating_remaining_units=gate_remaining,
                segregated_portfolio_units=segregated_remaining,
            )
            if not lot_eligibility.eligible:
                raise MfPaperBookError(",".join(lot_eligibility.reasons))
            remaining_to_check -= portion
            if gate_remaining is not None:
                gate_remaining -= portion
            if segregated_remaining is not None:
                segregated_remaining -= portion
        result = calculate_exit_loads(
            lots,
            redemption_date=redemption_date,
            redemption_units=units,
            nav_per_unit=nav,
        )
        lot_purchase_nav = {row[0]: Decimal(row[5]) for row in rows}
        redeemed_cost_basis = sum((
            allocation.units_redeemed * lot_purchase_nav[allocation.lot_id]
            for allocation in result.allocations
        ), Decimal(0))
        for allocation in result.allocations:
            con.execute(
                "UPDATE mf_paper_lots SET units_remaining = ? "
                "WHERE account_id = ? AND lot_id = ?",
                [allocation.units_remaining, account_id, allocation.lot_id],
            )
        if settlement_id is None:
            con.execute(
                "UPDATE mf_paper_accounts SET cash_inr = cash_inr + ? WHERE account_id = ?",
                [result.net_redemption_inr, account_id],
            )
        con.execute(
            """INSERT INTO mf_paper_events (
            account_id, event_id, kind, scheme_code, plan, option, isin, event_date,
            nav_per_unit, units, gross_inr, exit_load_inr, net_inr, event_sha256, created_at)
            VALUES (
            ?, ?, 'REDEEM', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                account_id,
                event_id,
                scheme_code,
                plan,
                option,
                isin,
                redemption_date,
                nav,
                result.redeemed_units,
                result.gross_redemption_inr,
                result.exit_load_inr,
                result.net_redemption_inr,
                digest,
                event_at,
            ],
        )
        from .mf_account_ledger import post_journal
        cash_or_receivable = "REDEMPTION_RECEIVABLE" if settlement_id else "CASH"
        journal_entries = [(cash_or_receivable, result.net_redemption_inr, 0)]
        if result.exit_load_inr:
            journal_entries.append(("EXIT_LOAD_EXPENSE", result.exit_load_inr, 0))
        if redeemed_cost_basis:
            journal_entries.append(("MF_INVESTMENTS", 0, redeemed_cost_basis))
        if result.gross_redemption_inr > redeemed_cost_basis:
            journal_entries.append(("REALIZED_GAIN", 0,
                                    result.gross_redemption_inr-redeemed_cost_basis))
        elif redeemed_cost_basis > result.gross_redemption_inr:
            journal_entries.append(("REALIZED_LOSS",
                                    redeemed_cost_basis-result.gross_redemption_inr, 0))
        post_journal(
            con, account_id=account_id, transaction_id=f"redemption:{event_id}",
            effective_at=event_at, recorded_at=event_at, entries=tuple(journal_entries),
            evidence={"event_id": event_id, "settlement_id": settlement_id,
                      "nav_per_unit": str(nav), "units": str(result.redeemed_units),
                      "gross_inr": str(result.gross_redemption_inr),
                      "exit_load_inr": str(result.exit_load_inr),
                      "redeemed_cost_basis_inr": str(redeemed_cost_basis),
                      "nav_observation_id": nav_observation_id},
        )
        if settlement_id is not None:
            assert nav_observation is not None and payout_due_date is not None
            value_evidence = {
                "redemption_event_id": event_id,
                "nav_observation_id": nav_observation_id,
                "nav_date": redemption_date,
                "nav_per_unit": nav,
                "gross_inr": result.gross_redemption_inr,
                "exit_load_inr": result.exit_load_inr,
                "net_inr": result.net_redemption_inr,
            }
            value_hash = _event_hash({"state": "VALUE_CONFIRMED", **value_evidence})
            con.execute("""UPDATE mf_paper_redemption_settlements SET
                state='VALUE_CONFIRMED', redemption_event_id=?, eligible_nav_date=?,
                nav_observation_id=?, nav_per_unit=?, gross_inr=?, exit_load_inr=?,
                net_inr=?, value_sha256=?, updated_at=?
                WHERE account_id=? AND settlement_id=?""",
                [event_id, redemption_date, nav_observation_id, nav,
                 result.gross_redemption_inr, result.exit_load_inr,
                 result.net_redemption_inr, value_hash, event_at,
                 account_id, settlement_id])
            con.execute("""INSERT INTO mf_paper_redemption_transitions (
                account_id, settlement_id, sequence, state, transitioned_at,
                event_sha256, evidence_json) VALUES (?,?,2,'VALUE_CONFIRMED',?,?,?)""",
                [account_id, settlement_id, event_at, value_hash,
                 json.dumps(value_evidence, sort_keys=True, separators=(",", ":"),
                            default=str)])
            payout_evidence = {
                "payout_due_date": payout_due_date,
                "net_inr": result.net_redemption_inr,
            }
            payout_hash = _event_hash({"state": "PAYOUT_PENDING", **payout_evidence})
            con.execute("""UPDATE mf_paper_redemption_settlements SET
                state='PAYOUT_PENDING', payout_due_date=?, updated_at=?
                WHERE account_id=? AND settlement_id=?""",
                [payout_due_date, event_at, account_id, settlement_id])
            con.execute("""INSERT INTO mf_paper_redemption_transitions (
                account_id, settlement_id, sequence, state, transitioned_at,
                event_sha256, evidence_json) VALUES (?,?,3,'PAYOUT_PENDING',?,?,?)""",
                [account_id, settlement_id, event_at, payout_hash,
                 json.dumps(payout_evidence, sort_keys=True, separators=(",", ":"),
                            default=str)])
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return {
        "account_id": account_id,
        "event_id": event_id,
        "kind": "REDEEM",
        "scheme_code": scheme_code,
        "plan": plan,
        "option": option,
        "isin": isin,
        "event_date": redemption_date,
        "nav_per_unit": nav,
        "units": result.redeemed_units,
        "gross_inr": result.gross_redemption_inr,
        "exit_load_inr": result.exit_load_inr,
        "net_inr": result.net_redemption_inr,
        "allocations": result.allocations,
        "settlement_id": settlement_id,
        "state": "PAYOUT_PENDING" if settlement_id else None,
        "status": "payout_pending" if settlement_id else "applied",
    }


def account_snapshot(
    con,
    account_id: str,
    *,
    known_at: datetime | None = None,
    stale_after: timedelta = timedelta(days=7),
) -> dict[str, Any]:
    """Return book balances and optional NAV valuation known by ``known_at``.

    NAV dates alone never imply availability: only append-only observations whose
    ingestion clock is no later than ``known_at`` may mark a position. Missing or
    old observations remain visible with explicit flags and no invented value.
    """
    if stale_after < timedelta(0):
        raise MfPaperBookError("stale_after cannot be negative")
    valuation_time = _aware(known_at) if known_at is not None else None
    row = con.execute(
        "SELECT cash_inr FROM mf_paper_accounts WHERE account_id = ?", [account_id]
    ).fetchone()
    if row is None:
        raise MfPaperBookError("paper account does not exist")
    lots = con.execute(
        """SELECT scheme_code, plan, option, isin, units_remaining, purchase_nav
        FROM mf_paper_lots WHERE account_id = ? AND units_remaining > 0
        ORDER BY scheme_code, plan, option, isin, allotted_on, lot_id""",
        [account_id],
    ).fetchall()
    grouped: dict[tuple[str, str, str, str], tuple[Decimal, Decimal]] = {}
    for scheme_code, plan, option, isin, raw_units, raw_nav in lots:
        key = (scheme_code, plan, option, isin)
        units, cost = grouped.get(key, (Decimal(0), Decimal(0)))
        lot_units, purchase_nav = Decimal(raw_units), Decimal(raw_nav)
        grouped[key] = (units + lot_units, cost + lot_units * purchase_nav)
    positions = []
    for key, values in sorted(grouped.items()):
        position = {
            "scheme_code": key[0],
            "plan": key[1],
            "option": key[2],
            "isin": key[3],
            "units": values[0],
            "cost_basis_inr": values[1],
        }
        if valuation_time is not None:
            isin_column = "isin_growth" if key[2] == "GROWTH" else "isin_reinvest"
            observation = con.execute(f"""SELECT observation_id, nav, nav_date,
                COALESCE(ingested_at, observed_at) AS available_at
                FROM mf_nav_observations WHERE scheme_code=? AND nav_date<=?
                  AND COALESCE(ingested_at, observed_at)<=?
                  AND (source_published_at IS NULL OR source_published_at<=?)
                  AND {isin_column}=? AND plan=? AND option=?
                ORDER BY nav_date DESC, available_at DESC, lake_key DESC,
                         row_number DESC LIMIT 1""",
                [key[0], valuation_time.astimezone(IST).date(), valuation_time,
                 valuation_time, key[3], key[1], key[2]]).fetchone()
            if observation is None:
                position.update(
                    nav_per_unit=None, nav_date=None, nav_observation_id=None,
                    nav_available_at=None, market_value_inr=None, unrealized_pnl_inr=None,
                    stale=True, valuation_status="NAV_UNAVAILABLE",
                )
            else:
                nav = Decimal(str(observation[1]))
                age = valuation_time.astimezone(IST).date() - observation[2]
                stale = age > stale_after
                value = values[0] * nav
                position.update(
                    nav_per_unit=nav, nav_date=observation[2],
                    nav_observation_id=observation[0], nav_available_at=observation[3],
                    market_value_inr=value,
                    unrealized_pnl_inr=value - values[1], stale=stale,
                    valuation_status="STALE" if stale else "CURRENT",
                )
        positions.append(position)
    pending = con.execute("""SELECT COALESCE(SUM(net_inr), 0)
        FROM mf_paper_redemption_settlements WHERE account_id=?
          AND state IN ('VALUE_CONFIRMED', 'PAYOUT_PENDING')""",
        [account_id]).fetchone()[0]
    cash = Decimal(row[0])
    pending_receivable = Decimal(pending)
    result = {
        "account_id": account_id,
        "cash_inr": cash,
        "pending_redemption_inr": pending_receivable,
        "cash_plus_pending_redemption_inr": cash + pending_receivable,
        "positions": positions,
    }
    if valuation_time is not None:
        valued: list[dict[str, Any]] = [
            item for item in positions if item["market_value_inr"] is not None
        ]
        result["valuation_known_at"] = valuation_time
        result["market_value_inr"] = sum(
            (item["market_value_inr"] for item in valued), Decimal(0)
        )
        result["unrealized_pnl_inr"] = sum(
            (item["unrealized_pnl_inr"] for item in valued), Decimal(0)
        )
        result["unvalued_position_count"] = sum(
            item["valuation_status"] == "NAV_UNAVAILABLE" for item in positions
        )
        result["stale_position_count"] = sum(
            bool(item["stale"]) for item in positions
        )
    return result


__all__ = ["MfPaperBookError", "account_snapshot", "create_account", "redeem", "subscribe"]
