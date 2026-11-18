"""Paper-only authenticated intake for TradingView observations.

The bearer token is delivered in the URL because TradingView cannot configure a
custom authentication header. Deploy only behind TLS with URL/access-log redaction.
No broker, OMS, or order module is imported here.
"""
from __future__ import annotations

import hashlib
import hmac
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .tradingview_alert import AlertRejected, TradingViewAlert, parse_alert


class IngressRejected(ValueError):
    """Authentication or replay conflict; never reveal which check failed."""


def accept_alert(
    raw: bytes,
    *,
    supplied_token: str,
    configured_token: str,
    database: Path,
    allowed_strategies: frozenset[str],
    received_time: datetime | None = None,
) -> tuple[str, TradingViewAlert]:
    """Atomically persist a fresh alert or identify an identical retry.

    This is a local paper-evidence inbox, not a decision or execution queue.
    Never store the bearer token or the raw URL. Unique alert IDs are global
    within this inbox; reusing one with different content is rejected.
    """
    if not configured_token or len(configured_token) < 32 or not hmac.compare_digest(
        supplied_token, configured_token
    ):
        raise IngressRejected("alert rejected")
    now = received_time or datetime.now(timezone.utc)
    try:
        alert = parse_alert(raw, received_time=now, allowed_strategies=allowed_strategies)
    except AlertRejected as exc:
        raise IngressRejected("alert rejected") from exc
    digest = hashlib.sha256(raw).hexdigest()
    database = Path(database)
    database.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database, timeout=5) as db:
        db.execute("""CREATE TABLE IF NOT EXISTS tradingview_alerts (
            alert_id TEXT PRIMARY KEY, payload_sha256 TEXT NOT NULL,
            strategy TEXT NOT NULL, exchange TEXT NOT NULL, symbol TEXT NOT NULL,
            direction TEXT NOT NULL, event_time TEXT NOT NULL,
            received_time TEXT NOT NULL, reference_price REAL NOT NULL
        )""")
        cur = db.execute("""INSERT OR IGNORE INTO tradingview_alerts
            (alert_id, payload_sha256, strategy, exchange, symbol, direction,
             event_time, received_time, reference_price) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (alert.alert_id, digest, alert.strategy, alert.exchange, alert.symbol,
             alert.direction, alert.event_time.isoformat(), alert.received_time.isoformat(),
             alert.reference_price))
        inserted = bool(cur.rowcount)
        stored = db.execute("SELECT payload_sha256 FROM tradingview_alerts WHERE alert_id = ?",
                            (alert.alert_id,)).fetchone()
        if stored is None or stored[0] != digest:
            raise IngressRejected("alert rejected")
    return ("accepted" if inserted else "duplicate"), alert
