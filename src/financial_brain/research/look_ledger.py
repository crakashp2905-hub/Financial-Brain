"""Append-only hash-chained ledger counting every research look and assumption variant."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

GENESIS = "0" * 64
LOOK_TYPES = {"FEATURE", "INDICATOR", "PARAMETER_VARIANT", "HORIZON", "STOP_RULE",
              "TAKE_PROFIT_RULE", "UNIVERSE_FILTER", "TRADING_RULE", "COST_ASSUMPTION"}


class ResearchLookError(ValueError):
    """Malformed or inconsistent research look event."""


@dataclass(frozen=True)
class ResearchLook:
    look_id: str
    experiment_id: str
    look_type: str
    description: str
    assumption: dict[str, Any]
    attempted_at: datetime

    def normalized(self) -> dict[str, Any]:
        if not all(isinstance(value, str) and value.strip() for value in
                   (self.look_id, self.experiment_id, self.description)):
            raise ResearchLookError("look ID, experiment ID and description are required")
        if self.look_type not in LOOK_TYPES:
            raise ResearchLookError("unsupported research look type")
        if not isinstance(self.assumption, dict) or not self.assumption:
            raise ResearchLookError("the exact tried assumption/variant must be recorded")
        if self.attempted_at.tzinfo is None or self.attempted_at.utcoffset() is None:
            raise ResearchLookError("attempt clock must be timezone aware")
        return {"look_id": self.look_id, "experiment_id": self.experiment_id,
                "look_type": self.look_type, "description": self.description,
                "assumption": self.assumption,
                "attempted_at": self.attempted_at.astimezone(timezone.utc).isoformat()}


class ResearchLookLedger:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(self.path)
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute("PRAGMA synchronous=FULL")
        self._con.execute("""CREATE TABLE IF NOT EXISTS research_looks (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT, look_id TEXT UNIQUE NOT NULL,
            experiment_id TEXT NOT NULL, look_type TEXT NOT NULL, body_json TEXT NOT NULL,
            previous_hash TEXT NOT NULL, event_hash TEXT NOT NULL)""")
        self._con.commit()

    def close(self) -> None:
        self._con.close()

    def append(self, look: ResearchLook) -> bool:
        body = look.normalized()
        canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False)
        prior = self._con.execute("SELECT event_hash FROM research_looks ORDER BY sequence DESC LIMIT 1").fetchone()
        previous = prior[0] if prior else GENESIS
        digest = hashlib.sha256((previous + canonical).encode()).hexdigest()
        try:
            self._con.execute("INSERT INTO research_looks "
                               "(look_id,experiment_id,look_type,body_json,previous_hash,event_hash) "
                               "VALUES (?,?,?,?,?,?)",
                               (look.look_id, look.experiment_id, look.look_type, canonical, previous, digest))
            self._con.commit()
            return True
        except sqlite3.IntegrityError as exc:
            old = self._con.execute("SELECT body_json FROM research_looks WHERE look_id=?",
                                    (look.look_id,)).fetchone()
            if old and old[0] == canonical:
                return False
            raise ResearchLookError("look ID reused with different assumptions; record a new look ID") from exc

    def audit(self) -> dict[str, Any]:
        rows = self._con.execute("SELECT look_id,experiment_id,look_type,body_json,previous_hash,event_hash "
                                 "FROM research_looks ORDER BY sequence").fetchall()
        previous = GENESIS
        for look_id, experiment_id, look_type, body, prior, digest in rows:
            if prior != previous or hashlib.sha256((prior + body).encode()).hexdigest() != digest:
                raise ResearchLookError("research-look ledger hash chain is corrupt")
            parsed = json.loads(body)
            if (parsed.get("look_id"), parsed.get("experiment_id"), parsed.get("look_type")) != (
                    look_id, experiment_id, look_type):
                raise ResearchLookError("research-look indexed identity differs from the event body")
            previous = digest
        return {"look_count": len(rows), "counts_by_type": {
            kind: sum(1 for row in rows if row[2] == kind) for kind in sorted(LOOK_TYPES)},
            "head_sha256": previous, "integrity": "VALID"}


__all__ = ["LOOK_TYPES", "ResearchLook", "ResearchLookError", "ResearchLookLedger"]
