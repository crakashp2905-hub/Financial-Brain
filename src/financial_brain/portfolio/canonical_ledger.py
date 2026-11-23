"""Append-only, source-bound event ledger for consolidated paper portfolios.

This is an event journal and deterministic position/cash projection. It does not infer fills
from signals or position snapshots: callers must submit explicit source-referenced events.
Legacy snapshots that lack fill provenance belong in an unresolved import report, not here.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterator

from financial_brain.kernel.evidence import EvidenceClass


class CanonicalLedgerError(ValueError):
    """An event is invalid, conflicting, or cannot be applied to the portfolio."""


class AssetClass(StrEnum):
    EQUITY = "EQUITY"
    ETF = "ETF"
    OPTION = "OPTION"
    MUTUAL_FUND = "MUTUAL_FUND"


class EventKind(StrEnum):
    CASH_FLOW = "CASH_FLOW"
    FILL = "FILL"
    FEE = "FEE"
    MARK = "MARK"
    UNRESOLVED_POSITION = "UNRESOLVED_POSITION"


_HASH = re.compile(r"^[0-9a-f]{64}$")
_CURRENCY = re.compile(r"^[A-Z]{3}$")
_EVIDENCE_CLASSES = {item.value for item in EvidenceClass} | {"BROKER_EXECUTION_RECEIPT"}


def _decimal(value: Decimal | str | int, field: str) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except Exception as exc:  # Decimal raises several input-specific exceptions.
        raise CanonicalLedgerError(f"{field} must be a decimal") from exc
    if not result.is_finite():
        raise CanonicalLedgerError(f"{field} must be finite")
    return result


def _required_decimal(value: Decimal | str | int | None, field: str) -> Decimal:
    if value is None:
        raise CanonicalLedgerError(f"{field} is required")
    return _decimal(value, field)


def _pnl_evidence(classes: set[str], *, computed: bool = True) -> dict[str, object]:
    evidence_classes = sorted(classes)
    classification = (evidence_classes[0] if len(evidence_classes) == 1
                      else "MIXED_EVIDENCE_CLASSES" if evidence_classes else "UNCLASSIFIED")
    return {
        "classification": classification if computed else "NOT_COMPUTABLE",
        "evidence_classes": evidence_classes,
        "profitability_claim": "NOT_ESTABLISHED",
    }


@dataclass(frozen=True)
class PortfolioEvent:
    """One immutable portfolio event with source identity and explicit evidence provenance."""

    account_id: str
    sleeve_id: str
    source_system: str
    source_event_id: str
    source_sha256: str
    event_kind: EventKind
    occurred_at: str
    currency: str = "INR"
    asset_class: AssetClass | None = None
    instrument_id: str | None = None
    side: str | None = None
    quantity: Decimal | str | int | None = None
    price: Decimal | str | int | None = None
    multiplier: Decimal | str | int = Decimal("1")
    cash_delta: Decimal | str | int | None = None
    evidence_class: str = "LOCAL_PAPER"
    metadata: dict[str, object] | None = None

    def normalized(self) -> dict[str, object]:
        if not all(isinstance(value, str) and value.strip() for value in
                   (self.account_id, self.sleeve_id, self.source_system, self.source_event_id)):
            raise CanonicalLedgerError("account, sleeve, source system and source event id are required")
        if not _HASH.fullmatch(self.source_sha256):
            raise CanonicalLedgerError("source_sha256 must be a lowercase SHA-256 hex digest")
        if not _CURRENCY.fullmatch(self.currency):
            raise CanonicalLedgerError("currency must be a three-letter uppercase code")
        if not isinstance(self.evidence_class, str) or self.evidence_class.strip().upper() not in _EVIDENCE_CLASSES:
            raise CanonicalLedgerError("evidence_class must be a recognized, non-empty evidence class")
        try:
            timestamp = datetime.fromisoformat(self.occurred_at.replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise CanonicalLedgerError("occurred_at must be an ISO timestamp") from exc
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise CanonicalLedgerError("occurred_at must include a timezone")

        row: dict[str, object] = {
            "account_id": self.account_id.strip(), "sleeve_id": self.sleeve_id.strip(),
            "source_system": self.source_system.strip(), "source_event_id": self.source_event_id.strip(),
            "source_sha256": self.source_sha256, "event_kind": EventKind(self.event_kind).value,
            "occurred_at": timestamp.astimezone(timezone.utc).isoformat(), "currency": self.currency,
            "asset_class": AssetClass(self.asset_class).value if self.asset_class is not None else None,
            "instrument_id": self.instrument_id.strip() if self.instrument_id else None,
            "side": self.side.upper() if self.side else None,
            "quantity": format(_decimal(self.quantity, "quantity"), "f") if self.quantity is not None else None,
            "price": format(_decimal(self.price, "price"), "f") if self.price is not None else None,
            "multiplier": format(_decimal(self.multiplier, "multiplier"), "f"),
            "cash_delta": format(_decimal(self.cash_delta, "cash_delta"), "f") if self.cash_delta is not None else None,
            "evidence_class": self.evidence_class.strip().upper(),
            "metadata": self.metadata or {},
        }
        if not isinstance(row["metadata"], dict):
            raise CanonicalLedgerError("metadata must be an object")

        kind = EventKind(str(row["event_kind"]))
        broker_receipt = row["evidence_class"] == "BROKER_EXECUTION_RECEIPT"
        if broker_receipt != (self.source_system.strip() == "BROKER_EXECUTION_RECEIPT" and kind is EventKind.FILL):
            raise CanonicalLedgerError(
                "broker execution receipt evidence is reserved for broker-sourced fill events"
            )
        if kind is EventKind.FILL:
            if not row["asset_class"] or not row["instrument_id"] or row["side"] not in {"BUY", "SELL"}:
                raise CanonicalLedgerError("a fill needs asset class, instrument id and BUY/SELL side")
            quantity = _required_decimal(self.quantity, "quantity")
            price = _required_decimal(self.price, "price")
            multiplier = _decimal(self.multiplier, "multiplier")
            if quantity <= 0 or price <= 0 or multiplier <= 0 or self.cash_delta is not None:
                raise CanonicalLedgerError("fill quantity, price and multiplier must be positive; omit cash_delta")
        elif kind is EventKind.MARK:
            if not row["asset_class"] or not row["instrument_id"] or _required_decimal(self.price, "price") <= 0:
                raise CanonicalLedgerError("a mark needs asset class, instrument id and positive price")
            if self.quantity is not None or self.cash_delta is not None:
                raise CanonicalLedgerError("a mark cannot change quantity or cash")
        elif kind in {EventKind.CASH_FLOW, EventKind.FEE}:
            amount = _required_decimal(self.cash_delta, "cash_delta")
            if self.quantity is not None or self.price is not None or self.instrument_id is not None:
                raise CanonicalLedgerError("cash events cannot contain instrument, quantity or price")
            if kind is EventKind.FEE and amount > 0:
                raise CanonicalLedgerError("fee cash_delta must be zero or negative")
        elif kind is EventKind.UNRESOLVED_POSITION:
            if not row["instrument_id"] or self.quantity is None:
                raise CanonicalLedgerError("unresolved position needs an instrument id and reported quantity")
            if self.price is not None or self.cash_delta is not None:
                raise CanonicalLedgerError("unresolved position snapshots cannot change cash or set a mark")
        return row


@dataclass
class _Position:
    asset_class: str
    instrument_id: str
    quantity: Decimal
    average_cost: Decimal
    realized_pnl: Decimal
    mark: Decimal | None
    mark_at: str | None
    currency: str
    multiplier: Decimal
    sleeves: set[str]
    fill_evidence_classes: set[str]


def _is_opening_balance(event: dict[str, object]) -> bool:
    metadata = event.get("metadata")
    return (event.get("event_kind") == EventKind.CASH_FLOW.value and isinstance(metadata, dict)
            and metadata.get("cash_flow_type") == "OPENING_BALANCE")


def _resolved_legacy_event_ids(events: list[dict[str, object]]) -> set[str]:
    """Return unresolved IDs closed by a complete, explicitly sourced broker receipt set."""
    resolved: set[str] = set()
    fills = [event for event in events if event.get("event_kind") == EventKind.FILL.value]
    for unresolved in events:
        if unresolved.get("event_kind") != EventKind.UNRESOLVED_POSITION.value:
            continue
        unresolved_id = unresolved.get("source_event_id")
        metadata = unresolved.get("metadata")
        if not isinstance(unresolved_id, str) or not isinstance(metadata, dict):
            continue
        legacy_id = metadata.get("legacy_trade_id")
        trade_side = metadata.get("trade_side")
        if not isinstance(legacy_id, str) or trade_side not in {"long", "short"}:
            continue
        expected_side = "SELL" if trade_side == "long" else "BUY"
        try:
            required_quantity = Decimal(str(unresolved["quantity"]))
            unresolved_at = datetime.fromisoformat(str(unresolved["occurred_at"]))
        except (KeyError, ValueError, ArithmeticError):
            continue
        receipts: list[dict[str, object]] = []
        for fill in fills:
            fill_metadata = fill.get("metadata")
            if not isinstance(fill_metadata, dict):
                continue
            if (fill.get("source_system") != "BROKER_EXECUTION_RECEIPT"
                    or fill.get("evidence_class") != "BROKER_EXECUTION_RECEIPT"
                    or fill_metadata.get("resolves_unresolved_event_id") != unresolved_id
                    or fill_metadata.get("legacy_trade_id") != legacy_id
                    or not fill_metadata.get("broker_order_id")
                    or fill_metadata.get("broker_trade_id") != fill.get("source_event_id")
                    or fill.get("instrument_id") != unresolved.get("instrument_id")
                    or fill.get("side") != expected_side):
                continue
            try:
                fill_at = datetime.fromisoformat(str(fill["occurred_at"]))
                quantity = Decimal(str(fill["quantity"]))
            except (KeyError, ValueError, ArithmeticError):
                continue
            if fill_at < unresolved_at or quantity <= 0:
                continue
            receipts.append(fill)
        try:
            receipt_quantity = sum((Decimal(str(fill["quantity"])) for fill in receipts), Decimal(0))
        except (KeyError, ArithmeticError):
            continue
        if receipts and receipt_quantity == required_quantity:
            resolved.add(unresolved_id)
    return resolved


class CanonicalPortfolioLedger:
    """SQLite event store with idempotent imports and as-of cash/position snapshots."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as con:
            con.execute("""CREATE TABLE IF NOT EXISTS portfolio_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id TEXT NOT NULL, sleeve_id TEXT NOT NULL,
                source_system TEXT NOT NULL, source_event_id TEXT NOT NULL,
                source_sha256 TEXT NOT NULL, payload_sha256 TEXT NOT NULL,
                event_json TEXT NOT NULL,
                UNIQUE (account_id, source_system, source_event_id))""")

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=30)
        con.row_factory = sqlite3.Row
        return con

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        con = self._connect()
        try:
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def append(self, event: PortfolioEvent) -> bool:
        """Append an event; identical source replays are no-ops, conflicting replays are refused."""
        row = event.normalized()
        body = json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False)
        payload_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
        with self._connection() as con:
            con.execute("BEGIN IMMEDIATE")
            old = con.execute("""SELECT payload_sha256 FROM portfolio_events
                WHERE account_id=? AND source_system=? AND source_event_id=?""",
                              (row["account_id"], row["source_system"], row["source_event_id"])).fetchone()
            if old is not None:
                if old["payload_sha256"] != payload_hash:
                    raise CanonicalLedgerError("source event id was replayed with different content")
                return False
            con.execute("""INSERT INTO portfolio_events
                (account_id,sleeve_id,source_system,source_event_id,source_sha256,payload_sha256,event_json)
                VALUES (?,?,?,?,?,?,?)""",
                        (row["account_id"], row["sleeve_id"], row["source_system"], row["source_event_id"],
                         row["source_sha256"], payload_hash, body))
        return True

    def import_events(self, events: list[PortfolioEvent]) -> dict[str, int]:
        """Import a source batch atomically; reject the entire batch if any event is invalid/conflicting."""
        normalized = [event.normalized() for event in events]
        payloads = [json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False) for row in normalized]
        hashes = [hashlib.sha256(body.encode("utf-8")).hexdigest() for body in payloads]
        added = 0
        with self._connection() as con:
            con.execute("BEGIN IMMEDIATE")
            for row, body, payload_hash in zip(normalized, payloads, hashes):
                old = con.execute("""SELECT payload_sha256 FROM portfolio_events
                    WHERE account_id=? AND source_system=? AND source_event_id=?""",
                                  (row["account_id"], row["source_system"], row["source_event_id"])).fetchone()
                if old is not None:
                    if old["payload_sha256"] != payload_hash:
                        raise CanonicalLedgerError("source event id was replayed with different content")
                    continue
                con.execute("""INSERT INTO portfolio_events
                    (account_id,sleeve_id,source_system,source_event_id,source_sha256,payload_sha256,event_json)
                    VALUES (?,?,?,?,?,?,?)""",
                            (row["account_id"], row["sleeve_id"], row["source_system"], row["source_event_id"],
                             row["source_sha256"], payload_hash, body))
                added += 1
        return {"received": len(events), "added": added, "already_present": len(events) - added}

    def events(self, account_id: str, *, as_of: str | None = None) -> list[dict[str, object]]:
        """Read immutable source events up to an optional timezone-aware as-of timestamp."""
        sql = "SELECT event_json FROM portfolio_events WHERE account_id=?"
        args: list[object] = [account_id]
        if as_of is not None:
            normalized = PortfolioEvent(
                account_id="validation", sleeve_id="validation", source_system="validation",
                source_event_id="validation", source_sha256="0" * 64, event_kind=EventKind.CASH_FLOW,
                occurred_at=as_of, cash_delta="0").normalized()["occurred_at"]
            sql += " AND json_extract(event_json, '$.occurred_at') <= ?"
            args.append(normalized)
        sql += " ORDER BY json_extract(event_json, '$.occurred_at'), sequence"
        with self._connection() as con:
            return [json.loads(row[0]) for row in con.execute(sql, args).fetchall()]

    def account_ids(self) -> list[str]:
        """Return known accounts in stable order for migration and audit reports."""
        with self._connection() as con:
            return [row[0] for row in con.execute(
                "SELECT DISTINCT account_id FROM portfolio_events ORDER BY account_id").fetchall()]

    def verify_import(self, events: list[PortfolioEvent]) -> dict[str, int]:
        """Verify each normalized source event is present byte-for-byte by its canonical payload hash."""
        missing = mismatched = 0
        with self._connection() as con:
            for event in events:
                row = event.normalized()
                body = json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False)
                expected_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
                stored = con.execute("""SELECT payload_sha256 FROM portfolio_events
                    WHERE account_id=? AND source_system=? AND source_event_id=?""",
                                     (row["account_id"], row["source_system"], row["source_event_id"])).fetchone()
                if stored is None:
                    missing += 1
                elif stored[0] != expected_hash:
                    mismatched += 1
        return {"verified": len(events) - missing - mismatched, "missing": missing, "mismatched": mismatched}

    def append_execution_receipt_resolution(
        self, account_id: str, unresolved_event_id: str, receipts: list[PortfolioEvent]
    ) -> dict[str, int]:
        """Append a complete broker-receipt-backed close for one unresolved legacy trade.

        The caller must retain and verify the original broker receipt artifact. This method
        binds receipt IDs and content hashes to the unresolved trade; it does not authenticate
        a broker or independently attest that caller-supplied receipt bytes are genuine.
        """
        if not receipts:
            raise CanonicalLedgerError("at least one broker execution receipt is required")
        existing = self.events(account_id)
        unresolved = next((row for row in existing
                           if row.get("source_event_id") == unresolved_event_id
                           and row.get("event_kind") == EventKind.UNRESOLVED_POSITION.value), None)
        if unresolved is None:
            raise CanonicalLedgerError("unresolved source event was not found in the account")
        normalized_receipts = [receipt.normalized() for receipt in receipts]
        combined: dict[tuple[str, str], dict[str, object]] = {
            (str(row["source_system"]), str(row["source_event_id"])): row for row in existing
        }
        for row in normalized_receipts:
            if row["account_id"] != account_id:
                raise CanonicalLedgerError("execution receipt account does not match unresolved position")
            key = (str(row["source_system"]), str(row["source_event_id"]))
            prior = combined.get(key)
            if prior is not None and prior != row:
                raise CanonicalLedgerError("broker receipt ID was replayed with different content")
            combined[key] = row
        if unresolved_event_id not in _resolved_legacy_event_ids(list(combined.values())):
            raise CanonicalLedgerError("broker receipt set does not exactly resolve the legacy position")
        return self.import_events(receipts)

    def event_count(self) -> int:
        """Return the total append-only journal event count."""
        with self._connection() as con:
            return int(con.execute("SELECT COUNT(*) FROM portfolio_events").fetchone()[0])

    def snapshot(self, account_id: str, *, as_of: str | None = None) -> dict[str, Any]:
        """Project cash, positions and unresolved legacy rows; no values are backfilled or inferred."""
        events = self.events(account_id, as_of=as_of)
        resolved_unresolved_ids = _resolved_legacy_event_ids(events)
        cash: dict[str, Decimal] = {}
        positions: dict[tuple[str, str, str], _Position] = {}
        marks: dict[tuple[str, str], tuple[Decimal, str, str, str]] = {}
        unresolved: list[dict[str, object]] = []
        for event in events:
            currency = str(event["currency"])
            kind = EventKind(str(event["event_kind"]))
            if kind is EventKind.CASH_FLOW or kind is EventKind.FEE:
                cash[currency] = cash.get(currency, Decimal(0)) + Decimal(str(event["cash_delta"]))
                continue
            if kind is EventKind.UNRESOLVED_POSITION:
                if str(event["source_event_id"]) not in resolved_unresolved_ids:
                    unresolved.append(event)
                continue
            instrument = str(event["instrument_id"])
            asset_class = str(event["asset_class"])
            if kind is EventKind.MARK:
                mark_key = (asset_class, instrument)
                mark_value = (Decimal(str(event["price"])), str(event["occurred_at"]), currency,
                              str(event["evidence_class"]))
                previous_mark = marks.get(mark_key)
                if previous_mark is not None and previous_mark[2] != currency:
                    raise CanonicalLedgerError("one instrument mark cannot mix currencies")
                marks[mark_key] = mark_value
                continue
            sleeve = str(event["sleeve_id"])
            key = (asset_class, instrument, sleeve)
            multiplier_value = Decimal(str(event["multiplier"]))
            pos = positions.setdefault(key, _Position(asset_class, instrument, Decimal(0), Decimal(0),
                                                       Decimal(0), None, None, currency, multiplier_value,
                                                       {sleeve}, set()))
            if pos.currency != currency:
                raise CanonicalLedgerError("one instrument position cannot mix currencies")
            if kind is EventKind.FILL and pos.multiplier != multiplier_value:
                raise CanonicalLedgerError("one instrument position cannot mix contract multipliers")
            pos.fill_evidence_classes.add(str(event["evidence_class"]))
            pos.sleeves.add(sleeve)
            quantity = Decimal(str(event["quantity"]))
            price = Decimal(str(event["price"]))
            signed = quantity if event["side"] == "BUY" else -quantity
            old_qty = pos.quantity
            old_cost = pos.average_cost
            new_qty = old_qty + signed
            cash[currency] = cash.get(currency, Decimal(0)) - signed * price * multiplier_value
            if old_qty == 0 or old_qty * signed > 0:
                pos.average_cost = ((abs(old_qty) * old_cost + abs(signed) * price) / abs(new_qty)
                                    if new_qty else Decimal(0))
            else:
                closed = min(abs(old_qty), abs(signed))
                direction = Decimal(1) if old_qty > 0 else Decimal(-1)
                pos.realized_pnl += closed * (price - old_cost) * direction * multiplier_value
                if new_qty == 0:
                    pos.average_cost = Decimal(0)
                elif old_qty * new_qty < 0:
                    pos.average_cost = price
            pos.quantity = new_qty
        rendered_positions = []
        for pos in positions.values():
            mark = marks.get((pos.asset_class, pos.instrument_id))
            pos.mark, pos.mark_at = (mark[0], mark[1]) if mark is not None else (None, None)
            if mark is not None and mark[2] != pos.currency:
                raise CanonicalLedgerError("instrument mark currency differs from position currency")
            realized_evidence = _pnl_evidence(pos.fill_evidence_classes)
            unrealized_classes = set(pos.fill_evidence_classes)
            if mark is not None:
                unrealized_classes.add(mark[3])
            rendered_positions.append({
                "asset_class": pos.asset_class, "instrument_id": pos.instrument_id,
                "sleeve_id": next(iter(pos.sleeves)),
                "quantity": pos.quantity, "average_cost": pos.average_cost,
                "realized_pnl": pos.realized_pnl, "mark": pos.mark, "mark_at": pos.mark_at,
                "currency": pos.currency, "multiplier": pos.multiplier,
                "unrealized_pnl": ((pos.mark - pos.average_cost) * pos.quantity * pos.multiplier
                                   if pos.mark is not None else None),
                "pnl_evidence": {
                    "realized": realized_evidence,
                    "unrealized": _pnl_evidence(unrealized_classes, computed=pos.mark is not None),
                },
                "sleeves": sorted(pos.sleeves),
            })
        consolidated: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for position in rendered_positions:
            consolidated.setdefault((str(position["asset_class"]), str(position["instrument_id"])), []).append(position)
        consolidated_positions = []
        for (asset_class, instrument), rows in sorted(consolidated.items()):
            quantities = [Decimal(str(row["quantity"])) for row in rows]
            open_rows = [row for row in rows if Decimal(str(row["quantity"])) != 0]
            same_direction = not open_rows or all(q > 0 for q in quantities if q) or all(q < 0 for q in quantities if q)
            gross_quantity = sum((abs(q) for q in quantities), Decimal(0))
            net_quantity = sum(quantities, Decimal(0))
            basis = (sum((abs(Decimal(str(row["quantity"]))) * Decimal(str(row["average_cost"]))
                          for row in open_rows), Decimal(0)) / gross_quantity
                     if open_rows and same_direction and gross_quantity else None)
            marks_for_rows = {row["mark"] for row in open_rows}
            realized_classes = {
                evidence_class
                for row in rows
                for evidence_class in row["pnl_evidence"]["realized"]["evidence_classes"]
            }
            unrealized_classes = {
                evidence_class
                for row in open_rows
                for evidence_class in row["pnl_evidence"]["unrealized"]["evidence_classes"]
            }
            consolidated_positions.append({
                "asset_class": asset_class, "instrument_id": instrument, "quantity": net_quantity,
                "gross_abs_quantity": gross_quantity, "average_cost": basis,
                "realized_pnl": sum((Decimal(str(row["realized_pnl"])) for row in rows), Decimal(0)),
                "unrealized_pnl": (sum((Decimal(str(row["unrealized_pnl"])) for row in open_rows), Decimal(0))
                                    if open_rows and all(row["unrealized_pnl"] is not None for row in open_rows)
                                    else (Decimal(0) if not open_rows else None)),
                "pnl_evidence": {
                    "realized": _pnl_evidence(realized_classes),
                    "unrealized": _pnl_evidence(
                        unrealized_classes,
                        computed=not open_rows or all(row["unrealized_pnl"] is not None for row in open_rows),
                    ),
                },
                "mark": next(iter(marks_for_rows)) if len(marks_for_rows) == 1 else None,
                "sleeves": sorted({str(row["sleeve_id"]) for row in rows}),
                "currency": rows[0]["currency"],
            })
        has_opening_balance = any(_is_opening_balance(event) for event in events)
        projection_complete = not unresolved and all(
            p["mark"] is not None or p["quantity"] == 0 for p in rendered_positions
        )
        pnl_promotion_blockers: list[str] = []
        if unresolved:
            pnl_promotion_blockers.append("UNRESOLVED_LEGACY_POSITIONS")
        if not projection_complete:
            pnl_promotion_blockers.append("INCOMPLETE_POSITION_PROJECTION")
        if not has_opening_balance:
            pnl_promotion_blockers.append("OPENING_BALANCE_MISSING")
        return {"account_id": account_id, "as_of": as_of, "cash": cash,
                "opening_balance_supplied": has_opening_balance,
                "cash_scope": "COMPLETE_WITH_OPENING_BALANCE" if has_opening_balance
                else "NET_EVENT_FLOWS_ONLY_NO_OPENING_BALANCE",
                "positions": sorted(rendered_positions, key=lambda p: (str(p["asset_class"]), str(p["instrument_id"]))),
                "consolidated_positions": consolidated_positions,
                "unresolved_positions": unresolved,
                "projection_complete": projection_complete,
                "pnl_promotion_gate": {
                    "status": "BLOCKED" if pnl_promotion_blockers else "ELIGIBLE_FOR_REVIEW",
                    "blockers": pnl_promotion_blockers,
                    "note": "ELIGIBLE_FOR_REVIEW is not a profitability claim or automatic strategy promotion.",
                }}


__all__ = ["AssetClass", "CanonicalLedgerError", "CanonicalPortfolioLedger", "EventKind", "PortfolioEvent"]
