"""Append-only decision/abstention journal, including zero-signal sessions (FE-0049)."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class DecisionJournalError(ValueError):
    """Decision journal refused malformed or conflicting evidence."""


OUTCOMES = frozenset({"SUBMITTED", "WITHHELD", "ABSTAINED", "UNSUCCESSFUL_SESSION", "NO_SIGNAL"})


@dataclass(frozen=True)
class DecisionRecord:
    session_id: str
    strategy_id: str
    decision_id: str
    outcome: str
    decided_at: datetime
    reason_codes: tuple[str, ...]
    input_sha256: str
    payload: dict[str, Any]

    def __post_init__(self) -> None:
        if not all(x and x.strip() for x in (self.session_id, self.strategy_id, self.decision_id)):
            raise DecisionJournalError("session, strategy and decision IDs are required")
        if self.outcome not in OUTCOMES:
            raise DecisionJournalError("unknown decision outcome")
        if self.decided_at.tzinfo is None or self.decided_at.utcoffset() is None:
            raise DecisionJournalError("decided_at must be timezone aware")
        if len(self.input_sha256) != 64 or any(c not in "0123456789abcdef" for c in self.input_sha256):
            raise DecisionJournalError("input_sha256 must be lowercase SHA-256")
        if any(not reason.strip() for reason in self.reason_codes):
            raise DecisionJournalError("reason codes cannot be empty")
        if self.outcome in {"WITHHELD", "ABSTAINED", "UNSUCCESSFUL_SESSION", "NO_SIGNAL"} and not self.reason_codes:
            raise DecisionJournalError("non-submitted outcomes require an explicit reason")

    def body(self) -> dict[str, Any]:
        return {"session_id": self.session_id, "strategy_id": self.strategy_id,
                "decision_id": self.decision_id, "outcome": self.outcome,
                "decided_at": self.decided_at.astimezone(timezone.utc).isoformat(),
                "reason_codes": sorted(set(self.reason_codes)), "input_sha256": self.input_sha256,
                "payload": self.payload}

    def sha256(self) -> str:
        raw = json.dumps(self.body(), sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class DecisionJournal:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(self.path))
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute("PRAGMA synchronous=FULL")
        self._con.execute("""CREATE TABLE IF NOT EXISTS decisions (
            decision_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, strategy_id TEXT NOT NULL,
            outcome TEXT NOT NULL, decided_at TEXT NOT NULL, record_sha256 TEXT NOT NULL,
            record_json TEXT NOT NULL
        )""")
        self._con.commit()

    def close(self) -> None:
        self._con.close()

    def append(self, record: DecisionRecord) -> bool:
        body = record.body()
        digest = record.sha256()
        previous = self._con.execute("SELECT record_sha256 FROM decisions WHERE decision_id=?",
                                     [record.decision_id]).fetchone()
        if previous:
            if previous[0] != digest:
                raise DecisionJournalError("decision ID reused with different content")
            return False
        self._con.execute("INSERT INTO decisions VALUES (?, ?, ?, ?, ?, ?, ?)",
                          [record.decision_id, record.session_id, record.strategy_id, record.outcome,
                           body["decided_at"], digest,
                           json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False)])
        self._con.commit()
        return True

    def session_records(self, session_id: str) -> list[dict[str, Any]]:
        rows = self._con.execute("SELECT record_json FROM decisions WHERE session_id=? ORDER BY decided_at, decision_id",
                                 [session_id]).fetchall()
        return [json.loads(row[0]) for row in rows]

    def session_summary(self, session_id: str, eligible_strategy_ids: set[str]) -> dict[str, Any]:
        if not eligible_strategy_ids:
            raise DecisionJournalError("eligible strategy denominator cannot be empty")
        rows = self.session_records(session_id)
        seen = {row["strategy_id"] for row in rows}
        if not seen <= eligible_strategy_ids:
            raise DecisionJournalError("journal contains a strategy outside the frozen eligibility set")
        missing = sorted(eligible_strategy_ids - seen)
        outcome_counts = {status: sum(row["outcome"] == status for row in rows) for status in sorted(OUTCOMES)}
        return {"session_id": session_id, "eligible_strategy_count": len(eligible_strategy_ids),
                "recorded_strategy_count": len(seen), "missing_strategy_ids": missing,
                "outcome_counts": outcome_counts,
                "complete": not missing and len(seen) == len(eligible_strategy_ids)}
