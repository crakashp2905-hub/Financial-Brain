"""Auditable opening-to-closing equity reconciliation over the canonical paper ledger."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
from pathlib import Path

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
    registry_status: str = "UNREGISTERED"
    registry_path: str | None = None


@dataclass(frozen=True, slots=True)
class SourceEventReconciliation:
    """Exact row-set comparison between one canonical source and supplied statement events."""

    account_id: str
    source_system: str
    period_start: datetime
    period_end: datetime
    canonical_event_count: int
    source_event_count: int
    matched_event_count: int
    statement_only_event_ids: tuple[str, ...]
    ledger_only_event_ids: tuple[str, ...]
    mismatched_event_ids: tuple[str, ...]
    canonical_events_sha256: str
    source_events_sha256: str
    report_sha256: str
    reconciled: bool
    limitations: tuple[str, ...]


def _event_economics(row: dict[str, object]) -> dict[str, object]:
    """Select the event fields that an independent statement can economically confirm."""
    return {key: row.get(key) for key in (
        "source_system", "source_event_id", "event_kind", "occurred_at", "currency",
        "asset_class", "instrument_id", "side", "position_action", "quantity", "price",
        "multiplier", "cash_delta", "metadata",
    )}


def reconcile_source_events(*, ledger: CanonicalPortfolioLedger, account_id: str,
                            source_system: str, period_start: datetime, period_end: datetime,
                            source_events: Sequence[PortfolioEvent]) -> SourceEventReconciliation:
    """Compare source statement events with the canonical journal by stable event identity.

    A reconciled result proves only that the caller-supplied event rows match this journal for
    the named source and time window. It does not authenticate the source document or prove
    that the supplied statement covers the full account period.
    """
    start = _as_of(period_start, "period_start")
    end = _as_of(period_end, "period_end")
    if not account_id.strip() or not source_system.strip() or end <= start:
        raise EquityReconciliationError("account, source and increasing statement period are required")
    account = account_id.strip()
    source_name = source_system.strip()

    normalized_source: dict[str, dict[str, object]] = {}
    for event in source_events:
        row = event.normalized()
        if row["account_id"] != account or row["source_system"] != source_name:
            raise EquityReconciliationError("statement event account or source does not match its report")
        occurred = datetime.fromisoformat(str(row["occurred_at"]))
        if not (start < occurred <= end):
            raise EquityReconciliationError("statement event is outside the declared period")
        event_id = str(row["source_event_id"])
        if event_id in normalized_source:
            raise EquityReconciliationError("statement contains duplicate source event identities")
        normalized_source[event_id] = row

    canonical: dict[str, dict[str, object]] = {}
    try:
        with _stable_ledger_cutoff(ledger):
            all_rows = ledger.events(account)
            verified = ledger.verify_import([_event(row) for row in all_rows])
            if verified != {"verified": len(all_rows), "missing": 0, "mismatched": 0}:
                raise EquityReconciliationError("canonical event payload hashes did not verify")
            for row in all_rows:
                if row.get("source_system") != source_name:
                    continue
                occurred = datetime.fromisoformat(str(row["occurred_at"]).replace("Z", "+00:00"))
                if start < occurred <= end:
                    event_id = str(row["source_event_id"])
                    canonical[event_id] = _event_economics(row)
    except EquityReconciliationError:
        raise
    except Exception as exc:
        raise EquityReconciliationError("canonical source event set could not be verified") from exc

    source_economics = {event_id: _event_economics(row)
                        for event_id, row in normalized_source.items()}
    canonical_ids, source_ids = set(canonical), set(source_economics)
    common = canonical_ids & source_ids
    mismatched = tuple(sorted(event_id for event_id in common
                              if canonical[event_id] != source_economics[event_id]))
    statement_only = tuple(sorted(source_ids - canonical_ids))
    ledger_only = tuple(sorted(canonical_ids - source_ids))
    matched = len(common) - len(mismatched)
    canonical_hash = _digest([canonical[key] for key in sorted(canonical)])
    source_hash = _digest([source_economics[key] for key in sorted(source_economics)])
    reconciled = not statement_only and not ledger_only and not mismatched
    limitations = (
        "Caller-supplied statement rows are not authenticated by this comparison.",
        "Matching the supplied period does not prove that the external statement is complete.",
    )
    body = {
        "account_id": account, "source_system": source_name,
        "period_start": start.isoformat(), "period_end": end.isoformat(),
        "canonical_event_count": len(canonical), "source_event_count": len(source_economics),
        "matched_event_count": matched, "statement_only_event_ids": statement_only,
        "ledger_only_event_ids": ledger_only, "mismatched_event_ids": mismatched,
        "canonical_events_sha256": canonical_hash, "source_events_sha256": source_hash,
        "reconciled": reconciled, "limitations": limitations,
    }
    return SourceEventReconciliation(
        account, source_name, start, end, len(canonical), len(source_economics), matched,
        statement_only, ledger_only, mismatched, canonical_hash, source_hash,
        _digest(body), reconciled, limitations,
    )


class EquityReconciliationRegistry:
    """Persist reconciliation receipts and invalidate them when their cutoff data changes."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        with sqlite3.connect(self.path, timeout=30) as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS account_equity_reports (
                report_sha256 TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                closing_as_of TEXT NOT NULL,
                event_sha256 TEXT,
                report_json TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('CURRENT','INVALIDATED','UNVERIFIABLE')),
                invalidation_reason TEXT
            )""")

    @staticmethod
    def _cutoff_hash(ledger: CanonicalPortfolioLedger, account_id: str,
                     closing_as_of: datetime) -> str:
        rows = ledger.events(account_id, as_of=closing_as_of.isoformat())
        return _digest(rows)

    def record(self, *, ledger: CanonicalPortfolioLedger,
               report: EquityReconciliation) -> dict[str, str | None]:
        """Store immutable report content and compare it with the ledger under a write lock."""
        body = json.dumps(asdict(report), sort_keys=True, separators=(",", ":"),
                          default=str, allow_nan=False)
        with sqlite3.connect(self.path, timeout=30) as connection:
            connection.execute("BEGIN IMMEDIATE")
            current_hash = self._cutoff_hash(ledger, report.account_id, report.closing_as_of)
            if report.event_sha256 is None:
                status = "UNVERIFIABLE"
                reason = "REPORT_HAS_NO_VERIFIED_CUTOFF_EVENT_HASH"
            elif current_hash != report.event_sha256:
                status = "INVALIDATED"
                reason = "CANONICAL_EVENTS_CHANGED_THROUGH_CLOSING_CUTOFF"
            else:
                status = "CURRENT"
                reason = None
            existing = connection.execute(
                "SELECT report_json,status,invalidation_reason FROM account_equity_reports "
                "WHERE report_sha256=?", (report.report_sha256,)
            ).fetchone()
            if existing is not None and existing[0] != body:
                raise EquityReconciliationError("report hash conflicts with stored report content")
            if existing is None:
                connection.execute(
                    "INSERT INTO account_equity_reports "
                    "(report_sha256,account_id,closing_as_of,event_sha256,report_json,status,invalidation_reason) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (report.report_sha256, report.account_id, report.closing_as_of.isoformat(),
                     report.event_sha256, body, status, reason),
                )
            elif existing[1] == "INVALIDATED":
                status, reason = "INVALIDATED", str(existing[2])
            elif status != "CURRENT":
                connection.execute(
                    "UPDATE account_equity_reports SET status=?,invalidation_reason=? WHERE report_sha256=?",
                    (status, reason, report.report_sha256),
                )
        return {"status": status, "invalidation_reason": reason}

    def status(self, *, report_sha256: str,
               ledger: CanonicalPortfolioLedger | None = None) -> dict[str, str | None]:
        """Return current validity, detecting late/backdated events at read time."""
        canonical = ledger or CanonicalPortfolioLedger(self.path)
        with sqlite3.connect(self.path, timeout=30) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT account_id,closing_as_of,event_sha256,status,invalidation_reason "
                "FROM account_equity_reports WHERE report_sha256=?", (report_sha256,)
            ).fetchone()
            if row is None:
                raise EquityReconciliationError("unknown equity reconciliation report")
            status = str(row["status"])
            reason = row["invalidation_reason"]
            if status == "CURRENT":
                closing = datetime.fromisoformat(str(row["closing_as_of"]))
                current_hash = self._cutoff_hash(canonical, str(row["account_id"]), closing)
                if current_hash != row["event_sha256"]:
                    status = "INVALIDATED"
                    reason = "CANONICAL_EVENTS_CHANGED_THROUGH_CLOSING_CUTOFF"
                    connection.execute(
                        "UPDATE account_equity_reports SET status=?,invalidation_reason=? WHERE report_sha256=?",
                        (status, reason, report_sha256),
                    )
        return {"status": status, "invalidation_reason": reason}


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
    receivables = snapshot.get("cash_receivables")
    if not isinstance(receivables, list):
        raise EquityReconciliationError(f"{label} cash receivables projection is missing")
    for row in receivables:
        if not isinstance(row, dict):
            raise EquityReconciliationError(f"{label} contains a malformed cash receivable")
        if row.get("currency") != "INR":
            raise EquityReconciliationError(f"{label} contains a non-INR cash receivable")
        amount = _money(row.get("amount"), f"{label} cash receivable amount")
        if amount <= 0:
            raise EquityReconciliationError(f"{label} contains a non-positive cash receivable")
        equity += amount
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
            cutoff_events = ledger.events(account_id, as_of=end.isoformat())
            event_hash = _digest(cutoff_events)
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
        "event_count": len(cutoff_events) if "cutoff_events" in locals() else 0,
        "event_sha256": event_hash, "opening_snapshot_sha256": opening_hash,
        "closing_snapshot_sha256": closing_hash, "blockers": tuple(blockers),
    }
    report_hash = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                          default=str, allow_nan=False).encode()).hexdigest()
    result = EquityReconciliation(
        account_id, start, end, opening_equity, closing_equity, external, pnl, reported_pnl, difference,
        tolerance, reconciled, len(cutoff_events) if "cutoff_events" in locals() else 0, event_hash, opening_hash,
        closing_hash, tuple(blockers), report_hash,
    )
    registry_state = EquityReconciliationRegistry(ledger.path).record(ledger=ledger, report=result)
    return replace(result, registry_status=str(registry_state["status"]),
                   registry_path=str(ledger.path))


def require_reconciled(result: EquityReconciliation, *,
                       registry: EquityReconciliationRegistry | None = None,
                       ledger: CanonicalPortfolioLedger | None = None) -> None:
    if (registry is None) != (ledger is None):
        raise EquityReconciliationError("current report verification requires both registry and ledger")
    if registry is not None and ledger is not None:
        status = registry.status(ledger=ledger, report_sha256=result.report_sha256)["status"]
    elif result.registry_path is not None:
        status = EquityReconciliationRegistry(result.registry_path).status(
            report_sha256=result.report_sha256)["status"]
    else:
        status = result.registry_status
    if status != "CURRENT":
        raise EquityReconciliationError(
            f"equity reconciliation report is not current: status={status}")
    if not result.reconciled:
        raise EquityReconciliationError(
            f"account equity did not reconcile: difference={result.difference_inr}, "
            f"blockers={result.blockers}")


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                  default=str, allow_nan=False).encode()).hexdigest()


__all__ = ["EquityReconciliation", "EquityReconciliationError", "EquityReconciliationRegistry",
           "SourceEventReconciliation", "reconcile_account_equity", "reconcile_source_events",
           "require_reconciled"]
