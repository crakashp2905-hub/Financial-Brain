"""Deterministic paper-session valuation and next-session carry checks (FE-0075).

This module records marks and paper dispositions only. It never sends orders or
claims a simulated mark is an exchange settlement price.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


class SessionCloseError(ValueError):
    """Invalid or conflicting session close evidence."""


@dataclass(frozen=True)
class OpenPosition:
    position_id: str
    instrument: str
    quantity: int
    product: str
    expiry: date | None = None
    carry_authorized: bool = False

    def __post_init__(self) -> None:
        if not self.position_id.strip() or not self.instrument.strip():
            raise SessionCloseError("position and instrument IDs are required")
        if type(self.quantity) is not int or self.quantity == 0:
            raise SessionCloseError("open position quantity must be a non-zero integer")
        if self.product not in {"CASH", "FUTURE", "OPTION"}:
            raise SessionCloseError("product must be CASH, FUTURE or OPTION")


@dataclass(frozen=True)
class CloseMark:
    instrument: str
    price: str
    source: str
    event_id: str
    observed_at: datetime
    available_at: datetime

    def __post_init__(self) -> None:
        try:
            px = Decimal(self.price)
        except (InvalidOperation, ValueError) as exc:
            raise SessionCloseError("mark price must be a finite positive decimal") from exc
        if not px.is_finite() or px <= 0:
            raise SessionCloseError("mark price must be a finite positive decimal")
        if not all(value.strip() for value in (self.instrument, self.source, self.event_id)):
            raise SessionCloseError("mark instrument, source and event ID are required")
        for value in (self.observed_at, self.available_at):
            if value.tzinfo is None or value.utcoffset() is None:
                raise SessionCloseError("mark timestamps must be timezone aware")
        if self.available_at < self.observed_at:
            raise SessionCloseError("mark cannot be available before observation")


@dataclass(frozen=True)
class SessionCloseReport:
    session_date: date
    next_session: date
    cutoff: datetime
    rows: tuple[dict[str, Any], ...]
    complete: bool
    report_hash: str

    def body(self) -> dict[str, Any]:
        return {"session_date": self.session_date.isoformat(), "next_session": self.next_session.isoformat(),
                "cutoff": self.cutoff.astimezone(timezone.utc).isoformat(), "rows": list(self.rows),
                "complete": self.complete, "report_hash": self.report_hash}


def build_session_close_report(*, session_date: date, next_session: date, cutoff: datetime,
                               positions: list[OpenPosition], marks: list[CloseMark],
                               max_mark_age_seconds: int = 300) -> SessionCloseReport:
    """Value each open paper position and decide whether a next-session carry is admissible."""
    if next_session <= session_date:
        raise SessionCloseError("next_session must be after session_date")
    if cutoff.tzinfo is None or cutoff.utcoffset() is None:
        raise SessionCloseError("cutoff must be timezone aware")
    if type(max_mark_age_seconds) is not int or max_mark_age_seconds < 0:
        raise SessionCloseError("max_mark_age_seconds must be a non-negative integer")
    if len({p.position_id for p in positions}) != len(positions):
        raise SessionCloseError("duplicate open position ID")
    by_instrument: dict[str, CloseMark] = {}
    for close_mark in marks:
        if close_mark.instrument in by_instrument:
            raise SessionCloseError("multiple close marks for an instrument are ambiguous")
        by_instrument[close_mark.instrument] = close_mark

    rows: list[dict[str, Any]] = []
    for pos in sorted(positions, key=lambda item: item.position_id):
        mark = by_instrument.get(pos.instrument)
        reason = ""
        usable = mark is not None
        if mark is None:
            reason = "MISSING_CLOSE_MARK"
        elif mark.available_at > cutoff:
            usable, reason = False, "MARK_NOT_AVAILABLE_BY_CUTOFF"
        elif (cutoff.astimezone(timezone.utc) - mark.observed_at.astimezone(timezone.utc)).total_seconds() > max_mark_age_seconds:
            usable, reason = False, "STALE_CLOSE_MARK"
        expired = pos.expiry is not None and pos.expiry <= session_date
        if expired:
            disposition = "EXPIRY_MARKED" if usable else "EXPIRY_UNRESOLVED"
            carry = False
            if not usable:
                reason = reason or "EXPIRY_SETTLEMENT_MARK_MISSING"
        elif usable and pos.carry_authorized:
            disposition, carry = "CARRY_ALLOWED", True
        elif usable:
            disposition, carry = "CARRY_BLOCKED", False
            reason = "CARRY_NOT_AUTHORIZED"
        else:
            disposition, carry = "VALUATION_MISSING", False
        rows.append({"position_id": pos.position_id, "instrument": pos.instrument,
                     "quantity": pos.quantity, "product": pos.product,
                     "expiry": pos.expiry.isoformat() if pos.expiry else None,
                     "mark_price": mark.price if usable and mark else None,
                     "mark_event_id": mark.event_id if usable and mark else None,
                     "mark_source": mark.source if usable and mark else None,
                     "disposition": disposition, "carry_to_next_session": carry, "reason": reason})
    complete = all(row["mark_price"] is not None for row in rows)
    body = {"session_date": session_date.isoformat(), "next_session": next_session.isoformat(),
            "cutoff": cutoff.astimezone(timezone.utc).isoformat(), "rows": rows, "complete": complete}
    report_hash = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                      allow_nan=False).encode()).hexdigest()
    return SessionCloseReport(session_date, next_session, cutoff, tuple(rows), complete, report_hash)


class SessionCloseJournal:
    """Immutable SQLite report archive with idempotent report IDs and conflict detection."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(self.path))
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute("PRAGMA synchronous=FULL")
        self._con.execute("CREATE TABLE IF NOT EXISTS session_close_reports (report_id TEXT PRIMARY KEY, session_date TEXT NOT NULL, report_hash TEXT NOT NULL, report_json TEXT NOT NULL)")
        self._con.commit()

    def close(self) -> None:
        self._con.close()

    def append(self, report_id: str, report: SessionCloseReport) -> bool:
        if not report_id.strip():
            raise SessionCloseError("report_id is required")
        encoded = json.dumps(report.body(), sort_keys=True, separators=(",", ":"), allow_nan=False)
        prior = self._con.execute("SELECT report_hash, report_json FROM session_close_reports WHERE report_id=?", [report_id]).fetchone()
        if prior:
            if prior != (report.report_hash, encoded):
                raise SessionCloseError("report ID reused with changed close evidence")
            return False
        self._con.execute("INSERT INTO session_close_reports VALUES (?, ?, ?, ?)",
                          [report_id, report.session_date.isoformat(), report.report_hash, encoded])
        self._con.commit()
        return True

    def get(self, report_id: str) -> dict[str, Any] | None:
        row = self._con.execute("SELECT report_json FROM session_close_reports WHERE report_id=?", [report_id]).fetchone()
        return json.loads(row[0]) if row else None
