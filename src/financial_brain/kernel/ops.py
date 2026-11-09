"""Operations: alerts, backup and restore verification (FB-0525, FB-0528, FB-0524, FB-0532, FB-0543, FB-0545, FB-0526).

* ``AlertQueue`` dedupes by key, throttles repeats, and holds quiet hours except for critical alerts. Delivery is
  exactly-once per logical alert via a sent-key set that survives a checkpoint.
* ``stale_feed_blocks_alert``: a breakout alert on stale data is suppressed with a warning instead.
* ``snapshot`` and ``restore`` copy a set of named tables; restore verifies per-table row counts and SHA-256 digests
  against the manifest and refuses partial restores.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Any

CRITICAL = frozenset({"MARGIN", "DRAWDOWN", "DATA_GAP", "BROKER_DISCONNECT"})


@dataclass(frozen=True)
class Alert:
    key: str
    kind: str
    at: datetime
    message: str
    priority: int = 0


@dataclass
class AlertQueue:
    quiet_start: time = time(22, 0)
    quiet_end: time = time(7, 0)
    min_gap_seconds: float = 60.0
    delivered: list[Alert] = field(default_factory=list)
    suppressed: list[tuple[Alert, str]] = field(default_factory=list)
    _sent: set[str] = field(default_factory=set)
    _last: dict[str, datetime] = field(default_factory=dict)

    def _quiet(self, t: time) -> bool:
        if self.quiet_start <= self.quiet_end:
            return self.quiet_start <= t < self.quiet_end
        return t >= self.quiet_start or t < self.quiet_end

    def offer(self, alert: Alert, *, feed_fresh: bool = True) -> str:
        if alert.key in self._sent:
            self.suppressed.append((alert, "DUPLICATE_KEY"))
            return "DUPLICATE"
        if alert.kind not in CRITICAL and not feed_fresh:
            self.suppressed.append((alert, "STALE_FEED"))
            return "STALE"
        last = self._last.get(alert.kind)
        if last is not None and (alert.at - last).total_seconds() < self.min_gap_seconds and alert.kind not in CRITICAL:
            self.suppressed.append((alert, "THROTTLED"))
            return "THROTTLED"
        if self._quiet(alert.at.time()) and alert.kind not in CRITICAL:
            self.suppressed.append((alert, "QUIET_HOURS"))
            return "QUIET"
        self._sent.add(alert.key)
        self._last[alert.kind] = alert.at
        self.delivered.append(alert)
        return "DELIVERED"


def _digest(rows: list[dict[str, Any]]) -> str:
    return hashlib.sha256(json.dumps(rows, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def snapshot(tables: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    return {
        "tables": {name: {"rows": rows, "count": len(rows), "sha256": _digest(rows)} for name, rows in tables.items()},
    }


def restore(manifest: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Return restored tables only if every table verifies; otherwise raise and restore nothing."""
    out: dict[str, list[dict[str, Any]]] = {}
    for name, meta in manifest["tables"].items():
        rows = meta["rows"]
        if len(rows) != meta["count"] or _digest(rows) != meta["sha256"]:
            raise ValueError(f"restore refused: table {name} failed verification")
        out[name] = rows
    return out
