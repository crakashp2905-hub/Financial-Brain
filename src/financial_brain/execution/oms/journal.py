"""Exactly-once, hash-chained audit log of every order change. Its own file: ``data/oms.duckdb``.

Two tables:

    oms_events   seq, event_key (UNIQUE), recorded_at, client_id, event, state, payload, prev_hash, hash
    oms_orders   the current state of each order, rebuilt from the events and kept beside them

**Exactly once.** Every write carries an ``event_key`` derived from what happened (the order, the new state, the
filled quantity, the broker's own update time). Recording the same event twice - a poll that sees the same broker
update again, a process restarted mid-cycle - finds the key and changes nothing. Applying a transition is therefore
safe to retry, which is what an order system that talks to a network has to be.

**Tamper-evident.** Each event's hash covers the previous event's hash, so editing or deleting a past row breaks
every hash after it; ``verify`` walks the chain.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from . import model as M

PATH = Path("data") / "oms.duckdb"
GENESIS = "0" * 64

SCHEMA = """
CREATE TABLE IF NOT EXISTS oms_events (seq BIGINT PRIMARY KEY, event_key VARCHAR UNIQUE, recorded_at VARCHAR, client_id VARCHAR,
    event VARCHAR, state VARCHAR, payload VARCHAR, prev_hash VARCHAR, hash VARCHAR);
CREATE TABLE IF NOT EXISTS oms_orders (client_id VARCHAR PRIMARY KEY, state VARCHAR, body VARCHAR, updated VARCHAR);
"""


class JournalError(RuntimeError):
    pass


def _hash(prev: str, body: dict) -> str:
    return hashlib.sha256((prev + json.dumps(body, sort_keys=True, default=str)).encode()).hexdigest()


class Journal:
    def __init__(self, path: Path | str = PATH):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.con = duckdb.connect(str(path))
        self.con.execute(SCHEMA)

    def close(self) -> None:
        self.con.close()

    # ------------------------------------------------------------------ writes

    def _append(self, key: str, client_id: str | None, event: str, state: str | None, payload: dict) -> bool:
        if self.con.execute("SELECT 1 FROM oms_events WHERE event_key = ?", [key]).fetchone():
            return False                                    # already recorded: exactly once
        last = self.con.execute("SELECT seq, hash FROM oms_events ORDER BY seq DESC LIMIT 1").fetchone()
        seq, prev = (last[0] + 1, last[1]) if last else (1, GENESIS)
        at = datetime.now(timezone.utc).isoformat()
        body = {"seq": seq, "event_key": key, "at": at, "client_id": client_id, "event": event, "state": state,
                "payload": payload}
        self.con.execute("INSERT INTO oms_events VALUES (?,?,?,?,?,?,?,?,?)",
                         [seq, key, at, client_id, event, state, json.dumps(payload, sort_keys=True, default=str),
                          prev, _hash(prev, body)])
        return True

    def record(self, order: M.Order, event: str, *, key_extra: str = "") -> bool:
        """Record ``order`` in its current state. Returns False if this exact event was already recorded."""
        key = f"{order.client_id}|{order.state}|{order.filled_qty}|{key_extra}"
        self.con.execute("BEGIN TRANSACTION")
        try:
            new = self._append(key, order.client_id, event, order.state, order.as_dict())
            if new:
                self.con.execute("INSERT OR REPLACE INTO oms_orders VALUES (?,?,?,?)",
                                 [order.client_id, order.state, json.dumps(order.as_dict(), default=str),
                                  datetime.now(timezone.utc).isoformat()])
            self.con.execute("COMMIT")
        except Exception:
            self.con.execute("ROLLBACK")
            raise
        return new

    def note(self, event: str, payload: dict, *, key: str) -> bool:
        """A non-order event (kill switch, stage change, reconciliation), also exactly once by ``key``."""
        return self._append(key, None, event, None, payload)

    # ------------------------------------------------------------------ reads

    def get(self, client_id: str) -> M.Order | None:
        r = self.con.execute("SELECT body FROM oms_orders WHERE client_id = ?", [client_id]).fetchone()
        return M.Order(**json.loads(r[0])) if r else None

    def orders(self, states: set[str] | None = None, day: str | None = None) -> list[M.Order]:
        rows = [M.Order(**json.loads(b)) for (b,) in self.con.execute("SELECT body FROM oms_orders").fetchall()]
        return [o for o in rows if (states is None or o.state in states) and (day is None or o.day == day)]

    def positions(self) -> dict[tuple[str, str], int]:
        """Net filled quantity by (exchange, symbol), from the journal alone - what the broker should also say."""
        pos: dict[tuple[str, str], int] = {}
        for o in self.orders():
            if o.filled_qty:
                k = (o.exchange, o.symbol)
                pos[k] = pos.get(k, 0) + o.signed_filled
        return {k: v for k, v in pos.items() if v}

    def events(self, event: str | None = None) -> list[dict]:
        q = "SELECT seq, recorded_at, client_id, event, state, payload FROM oms_events"
        rows = self.con.execute(q + (" WHERE event = ?" if event else "") + " ORDER BY seq", [event] if event else []).fetchall()
        return [{"seq": s, "at": a, "client_id": c, "event": e, "state": st, "payload": json.loads(p)}
                for s, a, c, e, st, p in rows]

    def verify(self) -> dict:
        """Walk the hash chain. Any edited, inserted or deleted row breaks it from that point on."""
        prev, n = GENESIS, 0
        for seq, key, at, cid, ev, st, payload, prev_hash, h in self.con.execute(
                "SELECT * FROM oms_events ORDER BY seq").fetchall():
            n += 1
            if seq != n or prev_hash != prev:
                return {"ok": False, "broken_at": seq, "why": "sequence or link"}
            body = {"seq": seq, "event_key": key, "at": at, "client_id": cid, "event": ev, "state": st,
                    "payload": json.loads(payload)}
            if _hash(prev, body) != h:
                return {"ok": False, "broken_at": seq, "why": "hash"}
            prev = h
        return {"ok": True, "events": n}
