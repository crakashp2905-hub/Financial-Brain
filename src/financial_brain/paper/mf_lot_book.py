"""Durable paper-book accounting for MF subscription lots and redemptions.

Each subscription creates an immutable lot with its own source-pinned exit-load
schedule. Redemptions allocate FIFO across those lots, reconcile fees into account
cash, and are idempotent by caller-supplied event ID.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from ..providers.mf_scheme_terms import ExitLoadBand
from .mf_exit_loads import PurchaseLot, calculate_exit_loads


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
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return {"account_id": account_id, "cash_inr": cash, "status": "created"}


def _existing_event(con, account_id: str, event_id: str, digest: str):
    row = con.execute(
        """SELECT kind, scheme_code, isin, event_date, nav_per_unit,
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
    isin: str,
    allotted_on: date,
    investment_inr: Decimal | str | int,
    nav_per_unit: Decimal | str | int,
    exit_loads: tuple[ExitLoadBand, ...] | None,
    exit_loads_sha256: str | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Debit cash and create a subscription lot using the explicitly supplied NAV."""
    for label, value in (
        ("account_id", account_id),
        ("event_id", event_id),
        ("lot_id", lot_id),
        ("scheme_code", scheme_code),
        ("isin", isin),
    ):
        if not value.strip():
            raise MfPaperBookError(f"{label} is required")
    amount, nav = _decimal(investment_inr, "investment"), _decimal(nav_per_unit, "NAV")
    schedule = _schedule_json(exit_loads)
    if exit_loads is None:
        if exit_loads_sha256 is not None:
            raise MfPaperBookError("unknown exit-load schedule cannot have a schedule hash")
        schedule_hash = None
    else:
        if exit_loads_sha256 is None:
            raise MfPaperBookError("known exit-load schedule requires its source hash")
        schedule_hash = _sha256(exit_loads_sha256, "exit-load source hash")
    units = amount / nav
    event_at = _aware(now)
    payload = {
        "kind": "SUBSCRIBE",
        "account_id": account_id,
        "event_id": event_id,
        "lot_id": lot_id,
        "scheme_code": scheme_code,
        "isin": isin,
        "allotted_on": allotted_on,
        "investment_inr": amount,
        "nav_per_unit": nav,
        "exit_loads_json": schedule,
        "exit_loads_sha256": schedule_hash,
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
        cash = Decimal(account[0])
        if amount > cash:
            raise MfPaperBookError("subscription exceeds available paper cash")
        con.execute(
            """INSERT INTO mf_paper_lots (
            account_id, lot_id, scheme_code, isin, allotted_on, units_original,
            units_remaining, purchase_nav, exit_loads_json, exit_loads_sha256,
            subscription_event_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                account_id,
                lot_id,
                scheme_code,
                isin,
                allotted_on,
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
            """INSERT INTO mf_paper_events VALUES (
            ?, ?, 'SUBSCRIBE', ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)""",
            [
                account_id,
                event_id,
                scheme_code,
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
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return {
        "account_id": account_id,
        "event_id": event_id,
        "kind": "SUBSCRIBE",
        "scheme_code": scheme_code,
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
    isin: str,
    redemption_date: date,
    redemption_units: Decimal | str | int,
    nav_per_unit: Decimal | str | int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Apply FIFO lot allocation and credit proceeds net of per-lot exit loads."""
    for label, value in (
        ("account_id", account_id),
        ("event_id", event_id),
        ("scheme_code", scheme_code),
        ("isin", isin),
    ):
        if not value.strip():
            raise MfPaperBookError(f"{label} is required")
    units = _decimal(redemption_units, "redemption units")
    nav = _decimal(nav_per_unit, "NAV")
    event_at = _aware(now)
    payload = {
        "kind": "REDEEM",
        "account_id": account_id,
        "event_id": event_id,
        "scheme_code": scheme_code,
        "isin": isin,
        "redemption_date": redemption_date,
        "redemption_units": units,
        "nav_per_unit": nav,
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
        rows = con.execute(
            """SELECT lot_id, allotted_on, units_remaining,
            exit_loads_json FROM mf_paper_lots WHERE account_id = ? AND scheme_code = ?
            AND isin = ? AND units_remaining > 0 ORDER BY allotted_on, lot_id""",
            [account_id, scheme_code, isin],
        ).fetchall()
        if not rows:
            raise MfPaperBookError("no open purchase lots for this scheme")
        lots = tuple(
            PurchaseLot(row[0], row[1], Decimal(row[2]), _schedule_from_json(row[3]))
            for row in rows
        )
        result = calculate_exit_loads(
            lots,
            redemption_date=redemption_date,
            redemption_units=units,
            nav_per_unit=nav,
        )
        for allocation in result.allocations:
            con.execute(
                "UPDATE mf_paper_lots SET units_remaining = ? "
                "WHERE account_id = ? AND lot_id = ?",
                [allocation.units_remaining, account_id, allocation.lot_id],
            )
        con.execute(
            "UPDATE mf_paper_accounts SET cash_inr = cash_inr + ? WHERE account_id = ?",
            [result.net_redemption_inr, account_id],
        )
        con.execute(
            """INSERT INTO mf_paper_events VALUES (
            ?, ?, 'REDEEM', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                account_id,
                event_id,
                scheme_code,
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
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return {
        "account_id": account_id,
        "event_id": event_id,
        "kind": "REDEEM",
        "scheme_code": scheme_code,
        "isin": isin,
        "event_date": redemption_date,
        "nav_per_unit": nav,
        "units": result.redeemed_units,
        "gross_inr": result.gross_redemption_inr,
        "exit_load_inr": result.exit_load_inr,
        "net_inr": result.net_redemption_inr,
        "allocations": result.allocations,
        "status": "applied",
    }


def account_snapshot(con, account_id: str) -> dict[str, Any]:
    """Return reconciled cash, units, and remaining subscription cost basis by scheme."""
    row = con.execute(
        "SELECT cash_inr FROM mf_paper_accounts WHERE account_id = ?", [account_id]
    ).fetchone()
    if row is None:
        raise MfPaperBookError("paper account does not exist")
    lots = con.execute(
        """SELECT scheme_code, isin, units_remaining, purchase_nav
        FROM mf_paper_lots WHERE account_id = ? AND units_remaining > 0
        ORDER BY scheme_code, isin, allotted_on, lot_id""",
        [account_id],
    ).fetchall()
    grouped: dict[tuple[str, str], tuple[Decimal, Decimal]] = {}
    for scheme_code, isin, raw_units, raw_nav in lots:
        key = (scheme_code, isin)
        units, cost = grouped.get(key, (Decimal(0), Decimal(0)))
        lot_units, purchase_nav = Decimal(raw_units), Decimal(raw_nav)
        grouped[key] = (units + lot_units, cost + lot_units * purchase_nav)
    positions = [
        {
            "scheme_code": key[0],
            "isin": key[1],
            "units": values[0],
            "cost_basis_inr": values[1],
        }
        for key, values in sorted(grouped.items())
    ]
    return {"account_id": account_id, "cash_inr": Decimal(row[0]), "positions": positions}


__all__ = ["MfPaperBookError", "account_snapshot", "create_account", "redeem", "subscribe"]
