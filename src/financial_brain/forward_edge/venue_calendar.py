"""Source-versioned NSE/BSE session calendar contract (FE-0034).

No holiday calendar is bundled here. Callers must supply official, date-bounded
session records. Missing or stale dates fail closed instead of guessing weekdays.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Iterable
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
VENUES = frozenset({"NSE", "BSE"})


class CalendarError(ValueError):
    """Calendar has missing, conflicting or invalid session evidence."""


@dataclass(frozen=True)
class Session:
    venue: str
    day: date
    opens: time
    closes: time
    session_type: str
    source_ref: str
    source_sha256: str
    recorded_at: datetime

    def __post_init__(self) -> None:
        if self.venue not in VENUES:
            raise CalendarError("venue must be NSE or BSE")
        if not self.session_type.strip() or not self.source_ref.strip():
            raise CalendarError("session type and official source reference are required")
        if len(self.source_sha256) != 64 or any(c not in "0123456789abcdef" for c in self.source_sha256):
            raise CalendarError("source_sha256 must be lowercase SHA-256")
        if self.opens >= self.closes:
            raise CalendarError("session open must precede close")
        if self.recorded_at.tzinfo is None or self.recorded_at.utcoffset() is None:
            raise CalendarError("recorded_at must be timezone aware")


class VenueCalendar:
    def __init__(self, sessions: Iterable[Session], *, source_as_of: date) -> None:
        self.source_as_of = source_as_of
        self.sessions: dict[tuple[str, date], Session] = {}
        for session in sessions:
            key = (session.venue, session.day)
            if key in self.sessions:
                raise CalendarError(f"duplicate session record for {session.venue} {session.day}")
            self.sessions[key] = session
        if any(day > source_as_of for _, day in self.sessions):
            raise CalendarError("calendar contains a session beyond source_as_of")

    def is_session(self, venue: str, day: date) -> bool:
        self._check_venue(venue)
        if day > self.source_as_of:
            raise CalendarError("date is beyond the calendar's sourced horizon")
        return (venue, day) in self.sessions

    def next_session(self, venue: str, after: date, *, through: date | None = None) -> Session:
        self._check_venue(venue)
        end = through or self.source_as_of
        if end > self.source_as_of:
            raise CalendarError("requested horizon exceeds sourced calendar coverage")
        candidates = [s for (v, day), s in self.sessions.items() if v == venue and after < day <= end]
        if not candidates:
            raise CalendarError(f"no sourced {venue} session after {after} through {end}")
        return min(candidates, key=lambda s: s.day)

    def session_bounds_utc(self, venue: str, day: date) -> tuple[datetime, datetime]:
        session = self.sessions.get((venue, day))
        if session is None:
            raise CalendarError(f"no sourced session for {venue} {day}")
        return (datetime.combine(day, session.opens, tzinfo=IST).astimezone(ZoneInfo("UTC")),
                datetime.combine(day, session.closes, tzinfo=IST).astimezone(ZoneInfo("UTC")))

    def sha256(self) -> str:
        rows = [{"venue": s.venue, "day": s.day.isoformat(), "opens": s.opens.isoformat(),
                 "closes": s.closes.isoformat(), "session_type": s.session_type,
                 "source_ref": s.source_ref, "source_sha256": s.source_sha256,
                 "recorded_at": s.recorded_at.isoformat()}
                for s in sorted(self.sessions.values(), key=lambda x: (x.venue, x.day))]
        blob = json.dumps({"source_as_of": self.source_as_of.isoformat(), "sessions": rows},
                          sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    @staticmethod
    def _check_venue(venue: str) -> None:
        if venue not in VENUES:
            raise CalendarError("venue must be NSE or BSE")
