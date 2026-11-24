"""Append-only archive for every evaluated and exploratory cohort, including nulls (FE-0089)."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path


COHORT_RESULTS = frozenset({"POSITIVE", "NEGATIVE", "INCONCLUSIVE", "NO_SIGNAL", "UNRESOLVED"})


class CohortArchiveError(ValueError):
    """Invalid or conflicting cohort archive record."""


@dataclass(frozen=True)
class CohortResult:
    cohort_id: str
    variant_id: str
    strategy_version_hash: str
    start_session: date
    end_session: date
    result_class: str
    eligible_sessions: int
    opportunity_count: int
    evidence_hash: str
    archived_at: datetime
    reason: str

    def __post_init__(self) -> None:
        if not all(value.strip() for value in (self.cohort_id, self.variant_id,
                                                self.strategy_version_hash, self.evidence_hash,
                                                self.reason)):
            raise CohortArchiveError("cohort identity, version, evidence hash and reason are required")
        if self.result_class not in COHORT_RESULTS:
            raise CohortArchiveError("unsupported cohort result class")
        if type(self.eligible_sessions) is not int or self.eligible_sessions < 0:
            raise CohortArchiveError("eligible_sessions must be a non-negative integer")
        if type(self.opportunity_count) is not int or self.opportunity_count < 0:
            raise CohortArchiveError("opportunity_count must be a non-negative integer")
        if self.end_session < self.start_session:
            raise CohortArchiveError("cohort end session cannot precede start")
        if self.result_class == "NO_SIGNAL" and self.opportunity_count != 0:
            raise CohortArchiveError("NO_SIGNAL cohort must explicitly contain zero opportunities")
        if self.archived_at.tzinfo is None or self.archived_at.utcoffset() is None:
            raise CohortArchiveError("archived_at must be timezone aware")

    def body(self) -> dict[str, object]:
        return {"cohort_id": self.cohort_id, "variant_id": self.variant_id,
                "strategy_version_hash": self.strategy_version_hash,
                "start_session": self.start_session.isoformat(), "end_session": self.end_session.isoformat(),
                "result_class": self.result_class, "eligible_sessions": self.eligible_sessions,
                "opportunity_count": self.opportunity_count, "evidence_hash": self.evidence_hash,
                "archived_at": self.archived_at.astimezone(timezone.utc).isoformat(), "reason": self.reason}

    def digest(self) -> str:
        return hashlib.sha256(json.dumps(self.body(), sort_keys=True, separators=(",", ":"),
                                         allow_nan=False).encode()).hexdigest()


class CohortArchive:
    """SQLite WAL archive that never overwrites a prior result for a cohort ID."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(self.path))
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute("PRAGMA synchronous=FULL")
        self._con.execute("CREATE TABLE IF NOT EXISTS cohort_results (cohort_id TEXT PRIMARY KEY, digest TEXT NOT NULL, result_json TEXT NOT NULL)")
        self._con.commit()

    def close(self) -> None:
        self._con.close()

    def archive(self, result: CohortResult) -> bool:
        encoded = json.dumps(result.body(), sort_keys=True, separators=(",", ":"), allow_nan=False)
        prior = self._con.execute("SELECT digest,result_json FROM cohort_results WHERE cohort_id=?",
                                  [result.cohort_id]).fetchone()
        if prior:
            if prior != (result.digest(), encoded):
                raise CohortArchiveError("cohort result is immutable; changed result needs a new cohort ID")
            return False
        self._con.execute("INSERT INTO cohort_results VALUES (?,?,?)", [result.cohort_id, result.digest(), encoded])
        self._con.commit()
        return True

    def find(self, *, variant_id: str | None = None,
             strategy_version_hash: str | None = None) -> list[dict[str, object]]:
        rows = self._con.execute("SELECT result_json FROM cohort_results ORDER BY cohort_id").fetchall()
        results = [json.loads(row[0]) for row in rows]
        return [row for row in results
                if (variant_id is None or row["variant_id"] == variant_id)
                and (strategy_version_hash is None or row["strategy_version_hash"] == strategy_version_hash)]
