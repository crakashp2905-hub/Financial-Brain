"""Immutable, outcome-blind strategy shadow observations (FE-0079)."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path


SHADOW_FAMILIES = frozenset({"chart_pattern", "orb", "donchian", "swing", "momentum",
                             "option_spread", "futures"})


class ShadowLogError(ValueError):
    """Invalid, incomplete or conflicting shadow scan."""


@dataclass(frozen=True)
class ShadowOpportunity:
    opportunity_id: str
    instrument_id: str
    side: str
    signal_code: str
    source_event_hash: str

    def __post_init__(self) -> None:
        if not all(value.strip() for value in (self.opportunity_id, self.instrument_id,
                                                self.signal_code, self.source_event_hash)):
            raise ShadowLogError("opportunity identity, instrument, signal and source hash are required")
        if self.side not in {"BUY", "SELL"}:
            raise ShadowLogError("shadow side must be BUY or SELL")

    def body(self) -> dict[str, str]:
        return {"opportunity_id": self.opportunity_id, "instrument_id": self.instrument_id,
                "side": self.side, "signal_code": self.signal_code,
                "source_event_hash": self.source_event_hash}


@dataclass(frozen=True)
class ShadowScan:
    session_id: str
    session_date: date
    family: str
    observed_at: datetime
    source_snapshot_hash: str
    opportunities: tuple[ShadowOpportunity, ...]

    def __post_init__(self) -> None:
        if not self.session_id.strip() or not self.source_snapshot_hash.strip():
            raise ShadowLogError("session and source snapshot hash are required")
        if self.family not in SHADOW_FAMILIES:
            raise ShadowLogError("unknown strategy family")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ShadowLogError("observed_at must be timezone aware")
        ids = [item.opportunity_id for item in self.opportunities]
        if len(set(ids)) != len(ids):
            raise ShadowLogError("duplicate opportunity in family scan")

    def body(self) -> dict[str, object]:
        return {"session_id": self.session_id, "session_date": self.session_date.isoformat(),
                "family": self.family, "observed_at": self.observed_at.astimezone(timezone.utc).isoformat(),
                "source_snapshot_hash": self.source_snapshot_hash,
                "opportunities": [item.body() for item in self.opportunities]}

    def digest(self) -> str:
        return hashlib.sha256(json.dumps(self.body(), sort_keys=True, separators=(",", ":"),
                                         allow_nan=False).encode()).hexdigest()


class ShadowOpportunityLog:
    """Persists complete opportunity scans without storing future outcome fields."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(self.path))
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute("PRAGMA synchronous=FULL")
        self._con.execute("CREATE TABLE IF NOT EXISTS shadow_scans (session_id TEXT NOT NULL, family TEXT NOT NULL, digest TEXT NOT NULL, scan_json TEXT NOT NULL, PRIMARY KEY(session_id,family))")
        self._con.commit()

    def close(self) -> None:
        self._con.close()

    def record_scan(self, scan: ShadowScan) -> bool:
        encoded = json.dumps(scan.body(), sort_keys=True, separators=(",", ":"), allow_nan=False)
        prior = self._con.execute("SELECT digest,scan_json FROM shadow_scans WHERE session_id=? AND family=?",
                                  [scan.session_id, scan.family]).fetchone()
        if prior:
            if prior != (scan.digest(), encoded):
                raise ShadowLogError("shadow scan is immutable; changed opportunities conflict")
            return False
        self._con.execute("INSERT INTO shadow_scans VALUES (?,?,?,?)",
                          [scan.session_id, scan.family, scan.digest(), encoded])
        self._con.commit()
        return True

    def session_coverage(self, session_id: str) -> dict[str, object]:
        rows = self._con.execute("SELECT family,scan_json FROM shadow_scans WHERE session_id=? ORDER BY family",
                                 [session_id]).fetchall()
        scans = {family: json.loads(body) for family, body in rows}
        missing = sorted(SHADOW_FAMILIES - scans.keys())
        return {"session_id": session_id, "complete": not missing, "missing_families": missing,
                "opportunity_count": sum(len(scan["opportunities"]) for scan in scans.values()),
                "families": {family: len(scan["opportunities"]) for family, scan in scans.items()}}
