"""Auditable opening-to-closing equity reconciliation over the canonical paper ledger."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN

from financial_brain.portfolio.canonical_ledger import (
    AssetClass,
    CanonicalPortfolioLedger,
    EventKind,
    PortfolioEvent,
)


_CENT = Decimal("0.01")
_ZERO = Decimal("0")


class EquityReconciliationError(ValueError):
    """Inputs are incomplete or accounting state cannot be reconciled."""


@contextmanager
def _stable_ledger_cutoff(ledger: CanonicalPortfolioLedger):
    """Block concurrent appends while the event set and both as-of snapshots are read."""
    connection = sqlite3.connect(str(ledger.path), timeout=30)
    try:
        connection.execute("BEGIN IMMEDIATE")
        yield
    finally:
        connection.rollback()
        connection.close()


@dataclass(frozen=True, slots=True)
class EquityReconciliation:
    account_id: str
    opening_as_of: datetime
    closing_as_of: datetime
    opening_equity_inr: Decimal | None
    closing_equity_inr: Decimal | None
    external_flows_inr: Decimal | None
    net_investment_pnl_inr: Decimal | None
    reported_net_pnl_inr: Decimal
    difference_inr: Decimal | None
    tolerance_inr: Decimal
    reconciled: bool
    event_count: int
    event_sha256: str | None
    opening_snapshot_sha256: str | None
    closing_snapshot_sha256: str | None
    blockers: tuple[str, ...]
    report_sha256: str


def _as_of(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise EquityReconciliationError(f"{label} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _money(value: object, label: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise EquityReconciliationError(f"{label} must be a finite INR amount") from exc
    if not result.is_finite():
        raise EquityReconciliationError(f"{label} must be a finite INR amount")
    return result


def _event(row: dict[str, object]) -> PortfolioEvent:
    metadata = row.get("metadata")
    return PortfolioEvent(
        account_id=str(row["account_id"]), sleeve_id=str(row["sleeve_id"]),
        source_system=str(row["source_system"]), source_event_id=str(row["source_event_id"]),
        source_sha256=str(row["source_sha256"]), event_kind=EventKind(str(row["event_kind"])),
        occurred_at=str(row["occurred_at"]), currency=str(row["currency"]),
        asset_class=AssetClass(str(row["asset_class"])) if row.get("asset_class") else None,
        instrument_id=str(row["instrument_id"]) if row.get("instrument_id") else None,
        side=str(row["side"]) if row.get("side") else None,
        quantity=str(row["quantity"]) if row.get("quantity") is not None else None,
        price=str(row["price"]) if row.get("price") is not None else None,
        multiplier=str(row["multiplier"]),
        cash_delta=str(row["cash_delta"]) if row.get("cash_delta") is not None else None,
        evidence_class=str(row["evidence_class"]),
        metadata=metadata if isinstance(metadata, dict) else {},
    )


def _snapshot_equity(snapshot: dict[str, object], label: str) -> Decimal:
    cash = snapshot.get("cash")
    if not isinstance(cash, dict) or set(cash) != {"INR"}:
        raise EquityReconciliationError(f"{label} must contain complete INR-only cash")
    positions = snapshot.get("positions")
    if not isinstance(positions, list):
        raise EquityReconciliationError(f"{label} position projection is missing")
    equity = _money(cash["INR"], f"{label} INR cash")
    for row in positions:
        if not isinstance(row, dict):
            raise EquityReconciliationError(f"{label} contains a malformed position")
        if row.get("currency") != "INR":
            raise EquityReconciliationError(f"{label} contains a non-INR position")
        quantity = _money(row.get("quantity"), f"{label} quantity")
        if quantity == 0:
            continue
        mark = row.get("mark")
        if mark is None:
            raise EquityReconciliationError(f"{label} has an unmarked open position")
        equity += quantity * _money(mark, f"{label} mark") * _money(
            row.get("multiplier"), f"{label} multiplier")
    return equity.quantize(_CENT, rounding=ROUND_HALF_EVEN)


def _check_mark_freshness(snapshot: dict[str, object], as_of: datetime, label: str,
                          maximum_mark_age_seconds: int) -> None:
    positions = snapshot.get("positions")
    if not isinstance(positions, list):
        return
    for row in positions:
        if not isinstance(row, dict) or row.get("quantity") == 0:
            continue
        try:
            marked_at = datetime.fromisoformat(str(row["mark_at"]).replace("Z", "+00:00"))
        except (KeyError, TypeError, ValueError) as exc:
            raise EquityReconciliationError(f"{label} has an invalid position mark time") from exc
        if marked_at.tzinfo is None or marked_at.utcoffset() is None:
            raise EquityReconciliationError(f"{label} has a timezone-naive position mark")
        age = (as_of - marked_at.astimezone(timezone.utc)).total_seconds()
        if age < 0 or age > maximum_mark_age_seconds:
            raise EquityReconciliationError(f"{label} has a stale or future position mark")


def reconcile_account_equity(*, ledger: CanonicalPortfolioLedger, account_id: str,
                             opening_as_of: datetime, closing_as_of: datetime,
                             reported_net_pnl_inr: Decimal,
                             tolerance_inr: Decimal = _CENT,
                             maximum_mark_age_seconds: int = 86400) -> EquityReconciliation:
    """Reconcile opening equity + external flows + investment P&L = closing equity.

    Fills, charges, distributions and mark changes remain represented by ledger events and
    the opening/closing valuation. A cash-flow event is excluded from investment P&L only
    when its metadata explicitly says ``flow_type=EXTERNAL``. Dividends/distributions are
    investment return and must not be misclassified as external contributions.
    """
    start = _as_of(opening_as_of, "opening_as_of")
    end = _as_of(closing_as_of, "closing_as_of")
    tolerance = _money(tolerance_inr, "tolerance_inr")
    reported_pnl = _money(reported_net_pnl_inr, "reported_net_pnl_inr")
    if not account_id.strip() or end <= start:
        raise EquityReconciliationError("account and increasing reconciliation interval are required")
    if tolerance < 0:
        raise EquityReconciliationError("tolerance cannot be negative")
    if isinstance(maximum_mark_age_seconds, bool) or maximum_mark_age_seconds < 1:
        raise EquityReconciliationError("maximum mark age must be a positive number of seconds")

    blockers: list[str] = []
    rows: list[dict[str, object]] = []
    opening_hash = closing_hash = event_hash = None
    opening_equity = closing_equity = external = pnl = difference = None
    try:
        with _stable_ledger_cutoff(ledger):
            # Verify every stored account row, including later rows: a corrupt timestamp must not
            # move an event outside the requested window and make it disappear from verification.
            all_events = ledger.events(account_id)
            if not all_events:
                raise EquityReconciliationError("no account events through closing time")
            verified = ledger.verify_import([_event(row) for row in all_events])
            if verified != {"verified": len(all_events), "missing": 0, "mismatched": 0}:
                raise EquityReconciliationError("event payload hashes did not verify")
            event_hash = hashlib.sha256(json.dumps(
                all_events, sort_keys=True, separators=(",", ":"), default=str,
                allow_nan=False).encode()).hexdigest()
            opening_snapshot = ledger.snapshot(account_id, as_of=start.isoformat())
            closing_snapshot = ledger.snapshot(account_id, as_of=end.isoformat())
            opening_hash = _digest(opening_snapshot)
            closing_hash = _digest(closing_snapshot)
            _check_mark_freshness(opening_snapshot, start, "opening snapshot", maximum_mark_age_seconds)
            _check_mark_freshness(closing_snapshot, end, "closing snapshot", maximum_mark_age_seconds)
            for label, snapshot in (("opening", opening_snapshot), ("closing", closing_snapshot)):
                if snapshot.get("opening_balance_supplied") is not True:
                    blockers.append(f"{label.upper()}_BALANCE_MISSING")
                if snapshot.get("projection_complete") is not True:
                    blockers.append(f"{label.upper()}_PROJECTION_INCOMPLETE")
                if snapshot.get("unresolved_positions"):
                    blockers.append(f"{label.upper()}_UNRESOLVED_POSITIONS")
                gate = snapshot.get("pnl_promotion_gate")
                if not isinstance(gate, dict) or gate.get("blockers"):
                    blockers.append(f"{label.upper()}_LEDGER_BLOCKERS")
            if not blockers:
                opening_equity = _snapshot_equity(opening_snapshot, "opening snapshot")
                closing_equity = _snapshot_equity(closing_snapshot, "closing snapshot")
                external_rows = []
                for row in all_events:
                    occurred = datetime.fromisoformat(str(row["occurred_at"]).replace("Z", "+00:00"))
                    metadata = row.get("metadata")
                    if (start < occurred <= end and row.get("event_kind") == EventKind.CASH_FLOW.value
                            and isinstance(metadata, dict) and metadata.get("flow_type") == "EXTERNAL"):
                        external_rows.append(row)
                external = sum((_money(row.get("cash_delta"), "external cash flow")
                                for row in external_rows), _ZERO).quantize(_CENT, rounding=ROUND_HALF_EVEN)
                pnl = (closing_equity - opening_equity - external).quantize(_CENT, rounding=ROUND_HALF_EVEN)
                difference = (opening_equity + external + reported_pnl - closing_equity).quantize(
                    _CENT, rounding=ROUND_HALF_EVEN)
    except Exception as exc:  # Any uncertain read or projection fails closed.
        blockers.append(f"LEDGER_UNCERTAIN:{type(exc).__name__}")

    reconciled = difference is not None and abs(difference) <= tolerance and not blockers
    body = {
        "account_id": account_id, "opening_as_of": start.isoformat(),
        "closing_as_of": end.isoformat(), "opening_equity_inr": opening_equity,
        "closing_equity_inr": closing_equity, "external_flows_inr": external,
        "net_investment_pnl_inr": pnl, "reported_net_pnl_inr": reported_pnl,
        "difference_inr": difference,
        "tolerance_inr": tolerance, "reconciled": reconciled,
        "event_count": len(all_events) if "all_events" in locals() else 0,
        "event_sha256": event_hash, "opening_snapshot_sha256": opening_hash,
        "closing_snapshot_sha256": closing_hash, "blockers": tuple(blockers),
    }
    report_hash = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                          default=str, allow_nan=False).encode()).hexdigest()
    return EquityReconciliation(
        account_id, start, end, opening_equity, closing_equity, external, pnl, reported_pnl, difference,
        tolerance, reconciled, len(all_events) if "all_events" in locals() else 0, event_hash, opening_hash,
        closing_hash, tuple(blockers), report_hash,
    )


def require_reconciled(result: EquityReconciliation) -> None:
    if not result.reconciled:
        raise EquityReconciliationError(
            f"account equity did not reconcile: difference={result.difference_inr}, "
            f"blockers={result.blockers}")


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                  default=str, allow_nan=False).encode()).hexdigest()


__all__ = ["EquityReconciliation", "EquityReconciliationError", "reconcile_account_equity",
           "require_reconciled"]
