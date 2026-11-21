"""Append-only paper order lifecycle journal with deterministic replay hashes (FE-0072)."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ORDER_STATES = frozenset({"CREATED", "SUBMITTED", "PARTIAL", "FILLED", "CANCELED", "REJECTED", "UNKNOWN"})
TRANSITIONS = {"CREATED": {"SUBMITTED", "REJECTED"},
               "SUBMITTED": {"PARTIAL", "FILLED", "CANCELED", "REJECTED", "UNKNOWN"},
               "PARTIAL": {"PARTIAL", "FILLED", "CANCELED", "UNKNOWN"},
               "UNKNOWN": {"PARTIAL", "FILLED", "CANCELED", "REJECTED"},
               "FILLED": set(), "CANCELED": set(), "REJECTED": set()}


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

    def body(self) -> dict[str, Any]:
        return {"order_id": self.order_id, "event_id": self.event_id, "sequence": self.sequence,
                "state": self.state, "occurred_at": self.occurred_at.astimezone(timezone.utc).isoformat(),
                "quantity": self.quantity, "filled_quantity": self.filled_quantity,
                "price": self.price, "accounting": self.accounting}

    def event_hash(self) -> str:
        return hashlib.sha256(json.dumps(self.body(), sort_keys=True, separators=(",", ":"),
                                   allow_nan=False).encode()).hexdigest()


class OrderJournal:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(self.path))
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute("PRAGMA synchronous=FULL")
        self._con.execute("""CREATE TABLE IF NOT EXISTS order_events (
            order_id TEXT NOT NULL, sequence INTEGER NOT NULL, event_id TEXT NOT NULL UNIQUE,
            state TEXT NOT NULL, occurred_at TEXT NOT NULL, event_hash TEXT NOT NULL,
            event_json TEXT NOT NULL, PRIMARY KEY(order_id, sequence)
        )""")
        self._con.commit()

    def close(self) -> None:
        self._con.close()

    def append(self, event: OrderEvent) -> bool:
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
        else:
            prior_state, prior_seq, prior_json = rows[-1]
            previous = json.loads(prior_json)
            if event.sequence != prior_seq + 1:
                raise OrderJournalError("order event sequence must be monotonic without gaps")
            if event.state not in TRANSITIONS[prior_state]:
                raise OrderJournalError(f"illegal order transition {prior_state} -> {event.state}")
            if event.filled_quantity < previous["filled_quantity"]:
                raise OrderJournalError("filled quantity cannot decrease")
        body = event.body()
        self._con.execute("INSERT INTO order_events VALUES (?, ?, ?, ?, ?, ?, ?)",
                          [event.order_id, event.sequence, event.event_id, event.state,
                           body["occurred_at"], digest,
                           json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False)])
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
