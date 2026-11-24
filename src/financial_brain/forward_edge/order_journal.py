"""Append-only paper order lifecycle journal with deterministic replay hashes (FE-0072)."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

ORDER_STATES = frozenset({
    "CREATED", "SUBMITTED", "ACCEPTED", "WORKING", "PARTIAL", "FILLED",
    "CANCEL_PENDING", "CANCELED", "REJECTED", "MISSED", "TIMED_OUT", "UNKNOWN", "EXPIRED",
})
TRANSITIONS = {
    "CREATED": {"SUBMITTED", "REJECTED"},
    "SUBMITTED": {"ACCEPTED", "WORKING", "PARTIAL", "FILLED", "CANCELED", "REJECTED",
                  "MISSED", "TIMED_OUT", "UNKNOWN"},
    "ACCEPTED": {"WORKING", "PARTIAL", "FILLED", "CANCEL_PENDING", "REJECTED", "MISSED",
                 "TIMED_OUT", "UNKNOWN", "EXPIRED"},
    "WORKING": {"WORKING", "PARTIAL", "FILLED", "CANCEL_PENDING", "CANCELED", "REJECTED",
                "MISSED", "TIMED_OUT", "UNKNOWN", "EXPIRED"},
    "PARTIAL": {"PARTIAL", "FILLED", "CANCEL_PENDING", "CANCELED", "TIMED_OUT", "UNKNOWN", "EXPIRED"},
    "CANCEL_PENDING": {"PARTIAL", "FILLED", "CANCELED", "TIMED_OUT", "UNKNOWN"},
    "TIMED_OUT": {"UNKNOWN", "SUBMITTED", "ACCEPTED", "WORKING", "PARTIAL", "FILLED", "CANCELED",
                  "REJECTED", "MISSED", "EXPIRED"},
    "UNKNOWN": {"SUBMITTED", "ACCEPTED", "WORKING", "PARTIAL", "FILLED", "CANCELED",
                "REJECTED", "MISSED", "EXPIRED"},
    "FILLED": set(), "CANCELED": set(), "REJECTED": set(), "MISSED": set(), "EXPIRED": set(),
}
_SHA256 = re.compile(r"[0-9a-f]{64}")


class OrderJournalError(ValueError):
    """Order event is invalid, out of sequence or conflicting."""


@dataclass(frozen=True)
class OrderEvent:
    order_id: str
    event_id: str
    sequence: int
    state: str
    occurred_at: datetime
    quantity: int
    filled_quantity: int
    price: str | None
    accounting: dict[str, Any]
    fill_evidence_id: str | None = None
    reconciliation_evidence_id: str | None = None

    def __post_init__(self) -> None:
        if not self.order_id.strip() or not self.event_id.strip():
            raise OrderJournalError("order_id and event_id are required")
        if type(self.sequence) is not int or self.sequence < 0:
            raise OrderJournalError("sequence must be a non-negative integer")
        if self.state not in ORDER_STATES or self.quantity < 1 or not 0 <= self.filled_quantity <= self.quantity:
            raise OrderJournalError("invalid state or order quantities")
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise OrderJournalError("occurred_at must be timezone aware")
        if self.state == "FILLED" and self.filled_quantity != self.quantity:
            raise OrderJournalError("FILLED requires full order quantity")
        if self.state == "PARTIAL" and not 0 < self.filled_quantity < self.quantity:
            raise OrderJournalError("PARTIAL requires a positive fill smaller than the order quantity")
        if self.state in {"CREATED", "SUBMITTED", "ACCEPTED", "WORKING", "MISSED", "REJECTED"} \
                and self.filled_quantity != 0:
            raise OrderJournalError(f"{self.state} cannot carry a confirmed fill quantity")
        if self.filled_quantity:
            try:
                parsed_price = Decimal(self.price) if self.price is not None else None
            except (InvalidOperation, TypeError, ValueError) as exc:
                raise OrderJournalError("a confirmed fill requires a valid price") from exc
            if parsed_price is None or not parsed_price.is_finite() or parsed_price <= 0:
                raise OrderJournalError("a confirmed fill requires a finite positive price")
        elif self.price is not None:
            raise OrderJournalError("an event with no confirmed fill cannot carry a fill price")
        for label, value in (("fill", self.fill_evidence_id),
                             ("reconciliation", self.reconciliation_evidence_id)):
            if value is not None and _SHA256.fullmatch(value) is None:
                raise OrderJournalError(f"{label} evidence ID must be a lowercase SHA-256")

    def body(self) -> dict[str, Any]:
        return {"order_id": self.order_id, "event_id": self.event_id, "sequence": self.sequence,
                "state": self.state, "occurred_at": self.occurred_at.astimezone(timezone.utc).isoformat(),
                "quantity": self.quantity, "filled_quantity": self.filled_quantity,
                "price": self.price, "accounting": self.accounting,
                "fill_evidence_id": self.fill_evidence_id,
                "reconciliation_evidence_id": self.reconciliation_evidence_id}

    def event_hash(self) -> str:
        return hashlib.sha256(json.dumps(self.body(), sort_keys=True, separators=(",", ":"),
                                   allow_nan=False).encode()).hexdigest()


class OrderJournal:
    def __init__(self, path: Path | None = None, *,
                 connection: sqlite3.Connection | None = None) -> None:
        if (path is None) == (connection is None):
            raise OrderJournalError("provide exactly one journal path or existing SQLite connection")
        self.path = Path(path) if path is not None else None
        self._owns_connection = connection is None
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._con = connection or sqlite3.connect(str(self.path))
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute("PRAGMA synchronous=FULL")
        self._con.execute("""CREATE TABLE IF NOT EXISTS order_events (
            order_id TEXT NOT NULL, sequence INTEGER NOT NULL, event_id TEXT NOT NULL UNIQUE,
            state TEXT NOT NULL, occurred_at TEXT NOT NULL, event_hash TEXT NOT NULL,
            event_json TEXT NOT NULL, PRIMARY KEY(order_id, sequence)
        )""")
        if self._owns_connection:
            self._con.commit()

    def close(self) -> None:
        if self._owns_connection:
            self._con.close()

    def append(self, event: OrderEvent, *, commit: bool | None = None) -> bool:
        digest = event.event_hash()
        prior = self._con.execute("SELECT event_hash FROM order_events WHERE event_id=?", [event.event_id]).fetchone()
        if prior:
            if prior[0] != digest:
                raise OrderJournalError("event ID reused with different content")
            return False
        rows = self._con.execute("SELECT state, sequence, event_json FROM order_events WHERE order_id=? ORDER BY sequence",
                                 [event.order_id]).fetchall()
        if not rows:
            if event.sequence != 0 or event.state != "CREATED":
                raise OrderJournalError("first order event must be CREATED at sequence zero")
            if event.filled_quantity != 0:
                raise OrderJournalError("a new order cannot begin with a synthesized fill")
        else:
            prior_state, prior_seq, prior_json = rows[-1]
            previous = json.loads(prior_json)
            if event.sequence != prior_seq + 1:
                raise OrderJournalError("order event sequence must be monotonic without gaps")
            if event.state not in TRANSITIONS[prior_state]:
                raise OrderJournalError(f"illegal order transition {prior_state} -> {event.state}")
            if event.occurred_at.astimezone(timezone.utc) < datetime.fromisoformat(previous["occurred_at"]):
                raise OrderJournalError("order event timestamps cannot move backwards")
            if event.filled_quantity < previous["filled_quantity"]:
                raise OrderJournalError("filled quantity cannot decrease")
            fill_increased = event.filled_quantity > previous["filled_quantity"]
            if fill_increased and event.state not in {"PARTIAL", "FILLED"}:
                raise OrderJournalError("non-fill lifecycle states cannot synthesize or increase fills")
            if fill_increased and event.fill_evidence_id is None:
                raise OrderJournalError("a new confirmed fill requires immutable fill evidence")
            needs_reconciliation = prior_state == "UNKNOWN" or (
                prior_state == "TIMED_OUT" and event.state != "UNKNOWN")
            if needs_reconciliation and event.reconciliation_evidence_id is None:
                raise OrderJournalError("resolving an ambiguous order requires reconciliation evidence")
            if event.state == "REJECTED" and event.filled_quantity:
                raise OrderJournalError("a partially filled order cannot transition to REJECTED")
        body = event.body()
        self._con.execute("INSERT INTO order_events VALUES (?, ?, ?, ?, ?, ?, ?)",
                          [event.order_id, event.sequence, event.event_id, event.state,
                           body["occurred_at"], digest,
                           json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False)])
        should_commit = self._owns_connection if commit is None else commit
        if should_commit:
            self._con.commit()
        return True

    def replay(self, order_id: str) -> list[dict[str, Any]]:
        return [json.loads(row[0]) for row in self._con.execute(
            "SELECT event_json FROM order_events WHERE order_id=? ORDER BY sequence", [order_id]).fetchall()]

    def order_ids(self) -> tuple[str, ...]:
        """All journalled paper orders in deterministic order."""
        return tuple(row[0] for row in self._con.execute(
            "SELECT DISTINCT order_id FROM order_events ORDER BY order_id").fetchall())

    def replay_hash(self, order_id: str) -> str:
        rows = self.replay(order_id)
        return hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(",", ":"),
                                         allow_nan=False).encode()).hexdigest()
