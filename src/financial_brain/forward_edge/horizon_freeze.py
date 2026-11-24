"""Pre-outcome holding-horizon freezes for forward strategy cohorts (FE-0080)."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path


class HorizonFreezeError(ValueError):
    """Invalid or retrospectively changed horizon declaration."""


@dataclass(frozen=True, order=True)
class HorizonChoice:
    unit: str
    length: int

    def __post_init__(self) -> None:
        if self.unit not in {"sessions", "minutes"} or type(self.length) is not int or self.length <= 0:
            raise HorizonFreezeError("horizon requires a supported unit and positive integer length")
        if self.unit == "sessions" and self.length not in {5, 20, 60}:
            raise HorizonFreezeError("session horizons must be one of the preregistered 5/20/60 values")
        if self.unit == "minutes" and self.length > 375:
            raise HorizonFreezeError("intraday horizon cannot exceed the regular 375-minute session")


@dataclass(frozen=True)
class HorizonFreeze:
    cohort_id: str
    strategy_id: str
    strategy_version_hash: str
    mechanics: str
    frozen_at: datetime
    first_eligible_session: date
    choices: tuple[HorizonChoice, ...]

    def __post_init__(self) -> None:
        if not all(value.strip() for value in (self.cohort_id, self.strategy_id, self.strategy_version_hash)):
            raise HorizonFreezeError("cohort, strategy and version hash are required")
        if self.mechanics not in {"intraday", "swing", "options", "futures"}:
            raise HorizonFreezeError("unknown strategy holding mechanics")
        if self.frozen_at.tzinfo is None or self.frozen_at.utcoffset() is None:
            raise HorizonFreezeError("frozen_at must be timezone aware")
        if self.frozen_at.date() >= self.first_eligible_session:
            raise HorizonFreezeError("horizon must be frozen before the first eligible session date")
        if not self.choices or len(set(self.choices)) != len(self.choices):
            raise HorizonFreezeError("horizons must be non-empty and unique")
        units = {choice.unit for choice in self.choices}
        if self.mechanics == "intraday" and units != {"minutes"}:
            raise HorizonFreezeError("intraday strategies require minute-based horizons")
        if self.mechanics != "intraday" and units != {"sessions"}:
            raise HorizonFreezeError("non-intraday strategies require session-based horizons")

    def body(self) -> dict[str, object]:
        return {"cohort_id": self.cohort_id, "strategy_id": self.strategy_id,
                "strategy_version_hash": self.strategy_version_hash, "mechanics": self.mechanics,
                "frozen_at": self.frozen_at.astimezone(timezone.utc).isoformat(),
                "first_eligible_session": self.first_eligible_session.isoformat(),
                "choices": [{"unit": choice.unit, "length": choice.length} for choice in sorted(self.choices)]}

    def digest(self) -> str:
        return hashlib.sha256(json.dumps(self.body(), sort_keys=True, separators=(",", ":"),
                                         allow_nan=False).encode()).hexdigest()


class HorizonFreezeJournal:
    """Append-only cohort freeze journal; changed choices require a new cohort ID."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(self.path))
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute("PRAGMA synchronous=FULL")
        self._con.execute("CREATE TABLE IF NOT EXISTS horizon_freezes (cohort_id TEXT PRIMARY KEY, digest TEXT NOT NULL, freeze_json TEXT NOT NULL)")
        self._con.commit()

    def close(self) -> None:
        self._con.close()

    def freeze(self, declaration: HorizonFreeze) -> bool:
        encoded = json.dumps(declaration.body(), sort_keys=True, separators=(",", ":"), allow_nan=False)
        prior = self._con.execute("SELECT digest,freeze_json FROM horizon_freezes WHERE cohort_id=?",
                                  [declaration.cohort_id]).fetchone()
        if prior:
            if prior != (declaration.digest(), encoded):
                raise HorizonFreezeError("cohort horizon freeze is immutable; create a new cohort")
            return False
        self._con.execute("INSERT INTO horizon_freezes VALUES (?,?,?)",
                          [declaration.cohort_id, declaration.digest(), encoded])
        self._con.commit()
        return True

    def get(self, cohort_id: str) -> dict[str, object] | None:
        row = self._con.execute("SELECT freeze_json FROM horizon_freezes WHERE cohort_id=?", [cohort_id]).fetchone()
        return json.loads(row[0]) if row else None
