"""Append-only forecast-issued journal for completed 15-minute index intervals."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from .index_15m import check_probabilities

IST = ZoneInfo("Asia/Kolkata")
IssueStatus = Literal["ISSUED", "ABSTAINED", "MISSED"]
CLASSES = ("UP", "DOWN", "FLAT")

SCHEMA = """
CREATE TABLE IF NOT EXISTS forecast_issues (
    issue_id TEXT PRIMARY KEY,
    session_date TEXT NOT NULL,
    interval_end TEXT NOT NULL,
    decision_at TEXT NOT NULL,
    issued_at TEXT NOT NULL,
    model_sha256 TEXT NOT NULL,
    feature_manifest_sha256 TEXT NOT NULL,
    status TEXT NOT NULL,
    probabilities_json TEXT,
    source_observed_at TEXT,
    source_available_at TEXT,
    quote_age_ms INTEGER,
    max_quote_age_ms INTEGER NOT NULL,
    abstention_or_miss_reason TEXT,
    payload_sha256 TEXT NOT NULL,
    CHECK (status IN ('ISSUED','ABSTAINED','MISSED'))
);
CREATE TRIGGER IF NOT EXISTS forecast_issues_no_update
BEFORE UPDATE ON forecast_issues BEGIN SELECT RAISE(ABORT, 'forecast issue journal is append-only'); END;
CREATE TRIGGER IF NOT EXISTS forecast_issues_no_delete
BEFORE DELETE ON forecast_issues BEGIN SELECT RAISE(ABORT, 'forecast issue journal is append-only'); END;
"""


class ForecastJournalError(ValueError):
    """Invalid or conflicting forecast-issued journal record."""


def _aware(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ForecastJournalError(f"{field} must be timezone-aware")
    return value


def _sha(value: str, field: str) -> None:
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ForecastJournalError(f"{field} must be lowercase SHA-256")


@dataclass(frozen=True)
class ForecastIssue:
    session_date: date
    interval_end: datetime
    decision_at: datetime
    issued_at: datetime
    model_sha256: str
    feature_manifest_sha256: str
    status: IssueStatus
    probabilities: dict[str, float] | None
    source_observed_at: datetime | None
    source_available_at: datetime | None
    max_quote_age_ms: int
    abstention_or_miss_reason: str | None = None

    def normalized(self) -> dict:
        interval_end = _aware(self.interval_end, "interval_end")
        decision = _aware(self.decision_at, "decision_at")
        issued = _aware(self.issued_at, "issued_at")
        if interval_end.astimezone(IST).date() != self.session_date:
            raise ForecastJournalError("interval end must match the IST session date")
        if interval_end > decision or decision > issued:
            raise ForecastJournalError("interval, decision and issue clocks are not chronological")
        _sha(self.model_sha256, "model_sha256")
        _sha(self.feature_manifest_sha256, "feature_manifest_sha256")
        if type(self.max_quote_age_ms) is not int or self.max_quote_age_ms < 0:
            raise ForecastJournalError("maximum quote age must be a nonnegative integer")
        if self.status not in ("ISSUED", "ABSTAINED", "MISSED"):
            raise ForecastJournalError("unsupported forecast issue status")
        if self.status == "MISSED":
            if self.probabilities is not None or not self.abstention_or_miss_reason:
                raise ForecastJournalError("missed intervals need a reason and no probabilities")
        else:
            if self.probabilities is None:
                raise ForecastJournalError("issued and abstained rows require probabilities")
            check_probabilities(self.probabilities)
            if self.status == "ABSTAINED" and not self.abstention_or_miss_reason:
                raise ForecastJournalError("abstentions need a reason")
        if (self.source_observed_at is None) != (self.source_available_at is None):
            raise ForecastJournalError("source observation and availability clocks must be paired")
        observed = available = None
        age_ms = None
        if self.source_observed_at is not None and self.source_available_at is not None:
            observed = _aware(self.source_observed_at, "source_observed_at")
            available = _aware(self.source_available_at, "source_available_at")
            if observed > available or available > decision:
                raise ForecastJournalError("source clocks must be observed <= available <= decision")
            age_ms = int((decision - available).total_seconds() * 1000)
            if age_ms > self.max_quote_age_ms and self.status == "ISSUED":
                raise ForecastJournalError("stale quote cannot be recorded as an issued forecast")
        elif self.status == "ISSUED":
            raise ForecastJournalError("issued forecasts require observed quote freshness evidence")

        probabilities = ({name: float(self.probabilities[name]) for name in CLASSES}
                         if self.probabilities is not None else None)
        payload = {
            "session_date": self.session_date.isoformat(),
            "interval_end": interval_end.isoformat(), "decision_at": decision.isoformat(),
            "issued_at": issued.isoformat(), "model_sha256": self.model_sha256,
            "feature_manifest_sha256": self.feature_manifest_sha256,
            "status": self.status, "probabilities": probabilities,
            "source_observed_at": observed.isoformat() if observed else None,
            "source_available_at": available.isoformat() if available else None,
            "quote_age_ms": age_ms, "max_quote_age_ms": self.max_quote_age_ms,
            "reason": self.abstention_or_miss_reason,
        }
        return payload


def connect(path: str | Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def append_issue(con: sqlite3.Connection, issue: ForecastIssue) -> dict[str, str | bool]:
    payload = issue.normalized()
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    payload_hash = hashlib.sha256(canonical.encode()).hexdigest()
    issue_id = hashlib.sha256(("|".join((payload["session_date"], payload["interval_end"],
                                       payload["model_sha256"]))).encode()).hexdigest()
    probabilities = payload["probabilities"]
    values = (issue_id, payload["session_date"], payload["interval_end"], payload["decision_at"],
              payload["issued_at"], issue.model_sha256, issue.feature_manifest_sha256,
              issue.status, json.dumps(probabilities, sort_keys=True) if probabilities is not None else None,
              payload["source_observed_at"], payload["source_available_at"], payload["quote_age_ms"],
              issue.max_quote_age_ms,
              issue.abstention_or_miss_reason, payload_hash)
    existed = con.execute("SELECT payload_sha256 FROM forecast_issues WHERE issue_id = ?",
                          [issue_id]).fetchone()
    with con:
        con.execute("""INSERT OR IGNORE INTO forecast_issues VALUES
                      (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", values)
        stored = con.execute("SELECT payload_sha256 FROM forecast_issues WHERE issue_id = ?",
                             [issue_id]).fetchone()
        if stored is None or stored["payload_sha256"] != payload_hash:
            raise ForecastJournalError("conflicting replay for forecast interval/model")
    return {"issue_id": issue_id, "payload_sha256": payload_hash,
            "idempotent_retry": existed is not None}


def expected_session_intervals(day: date) -> tuple[datetime, ...]:
    """Regular NSE 15-minute interval ends; special sessions need an explicit calendar override."""
    start = datetime.combine(day, time(9, 30), tzinfo=IST)
    return tuple(start + timedelta(minutes=15 * index) for index in range(25))


def missing_intervals(con: sqlite3.Connection, day: date,
                      expected: tuple[datetime, ...] | None = None) -> list[str]:
    schedule = expected or expected_session_intervals(day)
    recorded = {row[0] for row in con.execute(
        "SELECT DISTINCT interval_end FROM forecast_issues WHERE session_date = ?", [day.isoformat()])}
    return [interval.isoformat() for interval in schedule if interval.isoformat() not in recorded]
