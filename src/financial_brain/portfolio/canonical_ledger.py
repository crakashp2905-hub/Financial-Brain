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
from typing import Iterator


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
        if not isinstance(self.evidence_class, str) or not self.evidence_class.strip():
            raise CanonicalLedgerError("evidence_class is required")
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
            "evidence_class": self.evidence_class.strip(),
            "metadata": self.metadata or {},
        }
        if not isinstance(row["metadata"], dict):
            raise CanonicalLedgerError("metadata must be an object")

        kind = EventKind(str(row["event_kind"]))
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
        sql += " ORDER BY sequence"
        with self._connection() as con:
            return [json.loads(row[0]) for row in con.execute(sql, args).fetchall()]

    def snapshot(self, account_id: str, *, as_of: str | None = None) -> dict[str, object]:
        """Project cash, positions and unresolved legacy rows; no values are backfilled or inferred."""
        events = self.events(account_id, as_of=as_of)
        cash: dict[str, Decimal] = {}
        positions: dict[tuple[str, str], _Position] = {}
        unresolved: list[dict[str, object]] = []
        for event in events:
            currency = str(event["currency"])
            kind = EventKind(str(event["event_kind"]))
            if kind is EventKind.CASH_FLOW or kind is EventKind.FEE:
                cash[currency] = cash.get(currency, Decimal(0)) + Decimal(str(event["cash_delta"]))
                continue
            if kind is EventKind.UNRESOLVED_POSITION:
                unresolved.append(event)
                continue
            instrument = str(event["instrument_id"])
            asset_class = str(event["asset_class"])
            key = (asset_class, instrument)
            multiplier_value = Decimal(str(event["multiplier"]))
            pos = positions.setdefault(key, _Position(asset_class, instrument, Decimal(0), Decimal(0),
                                                       Decimal(0), None, None, currency, multiplier_value, set()))
            if pos.currency != currency:
                raise CanonicalLedgerError("one instrument position cannot mix currencies")
            if kind is EventKind.FILL and pos.multiplier != multiplier_value:
                raise CanonicalLedgerError("one instrument position cannot mix contract multipliers")
            pos.sleeves.add(str(event["sleeve_id"]))
            if kind is EventKind.MARK:
                pos.mark = Decimal(str(event["price"]))
                pos.mark_at = str(event["occurred_at"])
                continue
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
            rendered_positions.append({
                "asset_class": pos.asset_class, "instrument_id": pos.instrument_id,
                "quantity": pos.quantity, "average_cost": pos.average_cost,
                "realized_pnl": pos.realized_pnl, "mark": pos.mark, "mark_at": pos.mark_at,
                "currency": pos.currency, "multiplier": pos.multiplier,
                "unrealized_pnl": ((pos.mark - pos.average_cost) * pos.quantity * pos.multiplier
                                   if pos.mark is not None else None),
                "sleeves": sorted(pos.sleeves),
            })
        return {"account_id": account_id, "as_of": as_of, "cash": cash,
                "positions": sorted(rendered_positions, key=lambda p: (str(p["asset_class"]), str(p["instrument_id"]))),
                "unresolved_positions": unresolved,
                "projection_complete": not unresolved and all(p["mark"] is not None or p["quantity"] == 0
                                                               for p in rendered_positions)}


__all__ = ["AssetClass", "CanonicalLedgerError", "CanonicalPortfolioLedger", "EventKind", "PortfolioEvent"]
