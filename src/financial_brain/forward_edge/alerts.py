"""Deduplicated operational alerts and entry-halt state (FE-0074)."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ALERT_TYPES = frozenset({"STALE_QUOTE", "TOKEN_EXPIRED", "SESSION_ABORTED", "DUPLICATE_ORDER", "LEDGER_DIFFERENCE"})
SEVERITY_RANK = {"INFO": 0, "WARNING": 1, "HIGH": 2, "CRITICAL": 3}


class AlertError(ValueError):
    """Invalid operational alert or transition."""


@dataclass(frozen=True)
class Alert:
    alert_id: str
    root_cause_id: str
    session_id: str
    alert_type: str
    severity: str
    occurred_at: datetime
    details: str

    def __post_init__(self) -> None:
        if not all(x.strip() for x in (self.alert_id, self.root_cause_id, self.session_id, self.details)):
            raise AlertError("alert identity, session and details are required")
        if self.alert_type not in ALERT_TYPES or self.severity not in SEVERITY_RANK:
            raise AlertError("unknown alert type or severity")
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise AlertError("occurred_at must be timezone aware")

    @classmethod
    def create(cls, *, root_cause_id: str, session_id: str, alert_type: str, severity: str,
               occurred_at: datetime, details: str) -> "Alert":
        raw = f"{root_cause_id}|{session_id}|{alert_type}".encode()
        return cls(hashlib.sha256(raw).hexdigest(), root_cause_id, session_id, alert_type,
                   severity, occurred_at, details)

    def digest(self) -> str:
        body = {"alert_id": self.alert_id, "root_cause_id": self.root_cause_id,
                "session_id": self.session_id, "alert_type": self.alert_type,
                "severity": self.severity, "occurred_at": self.occurred_at.astimezone(timezone.utc).isoformat(),
                "details": self.details}
        return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class AlertBook:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(self.path))
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute("PRAGMA synchronous=FULL")
        self._con.execute("""CREATE TABLE IF NOT EXISTS alerts (
            alert_id TEXT PRIMARY KEY, root_cause_id TEXT NOT NULL, session_id TEXT NOT NULL,
            alert_type TEXT NOT NULL, severity TEXT NOT NULL, occurred_at TEXT NOT NULL,
            details TEXT NOT NULL, digest TEXT NOT NULL, acknowledged INTEGER NOT NULL DEFAULT 0,
            cleared INTEGER NOT NULL DEFAULT 0
        )""")
        self._con.commit()

    def close(self) -> None:
        self._con.close()

    def raise_alert(self, alert: Alert) -> bool:
        prior = self._con.execute("SELECT digest FROM alerts WHERE alert_id=?", [alert.alert_id]).fetchone()
        if prior:
            if prior[0] != alert.digest():
                raise AlertError("dedupe alert ID reused with changed content")
            return False
        self._con.execute("INSERT INTO alerts(alert_id,root_cause_id,session_id,alert_type,severity,occurred_at,details,digest) VALUES (?,?,?,?,?,?,?,?)",
                          [alert.alert_id, alert.root_cause_id, alert.session_id, alert.alert_type,
                           alert.severity, alert.occurred_at.astimezone(timezone.utc).isoformat(),
                           alert.details, alert.digest()])
        self._con.commit()
        return True

    def acknowledge(self, alert_id: str) -> None:
        cur = self._con.execute("UPDATE alerts SET acknowledged=1 WHERE alert_id=?", [alert_id])
        if cur.rowcount != 1:
            raise AlertError("unknown alert")
        self._con.commit()

    def clear(self, alert_id: str) -> None:
        cur = self._con.execute("UPDATE alerts SET cleared=1 WHERE alert_id=?", [alert_id])
        if cur.rowcount != 1:
            raise AlertError("unknown alert")
        self._con.commit()

    def halt_entries(self, session_id: str) -> bool:
        row = self._con.execute("SELECT COUNT(*) FROM alerts WHERE session_id=? AND cleared=0 AND severity IN ('HIGH','CRITICAL')",
                                [session_id]).fetchone()
        return bool(row[0])

    def active(self, session_id: str) -> list[dict[str, object]]:
        rows = self._con.execute("SELECT alert_id,root_cause_id,alert_type,severity,occurred_at,details,acknowledged FROM alerts WHERE session_id=? AND cleared=0 ORDER BY occurred_at,alert_id",
                                 [session_id]).fetchall()
        names = ("alert_id", "root_cause_id", "alert_type", "severity", "occurred_at", "details", "acknowledged")
        return [dict(zip(names, row, strict=True)) for row in rows]
