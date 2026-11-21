"""Source-bound calendar and NAV timing for Indian exchange, scheme and settlement clocks."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping
from zoneinfo import ZoneInfo

from financial_brain.forward_edge.venue_calendar import CalendarError, VenueCalendar

IST = ZoneInfo("Asia/Kolkata")


class TradingClockError(ValueError):
    """Missing, stale, conflicting or invalid calendar/timing evidence."""


class CalendarDomain(StrEnum):
    SCHEME = "SCHEME"
    BANK_SETTLEMENT = "BANK_SETTLEMENT"


@dataclass(frozen=True)
class BusinessCalendar:
    """Explicit, source-versioned open-day calendar; weekends are not guessed."""

    calendar_id: str
    domain: CalendarDomain
    coverage_start: date
    source_as_of: date
    open_days: frozenset[date]
    source_ref: str
    source_sha256: str
    recorded_at: datetime

    def __post_init__(self) -> None:
        if not self.calendar_id.strip() or not self.source_ref.strip():
            raise TradingClockError("calendar id and source reference are required")
        if self.coverage_start > self.source_as_of:
            raise TradingClockError("calendar coverage starts after its source horizon")
        if any(day < self.coverage_start or day > self.source_as_of for day in self.open_days):
            raise TradingClockError("open day lies outside declared calendar coverage")
        if len(self.source_sha256) != 64 or any(c not in "0123456789abcdef" for c in self.source_sha256):
            raise TradingClockError("source_sha256 must be lowercase SHA-256")
        if self.recorded_at.tzinfo is None or self.recorded_at.utcoffset() is None:
            raise TradingClockError("recorded_at must be timezone aware")
        object.__setattr__(self, "domain", CalendarDomain(self.domain))

    def is_open(self, day: date) -> bool:
        if not self.coverage_start <= day <= self.source_as_of:
            raise TradingClockError(f"{self.calendar_id} calendar has no coverage for {day}")
        return day in self.open_days

    def next_open(self, after: date) -> date:
        if after < self.coverage_start or after >= self.source_as_of:
            raise TradingClockError(f"{self.calendar_id} has no sourced day after {after}")
        candidates = [day for day in self.open_days if after < day]
        if not candidates:
            raise TradingClockError(f"no sourced {self.calendar_id} open day after {after}")
        return min(candidates)

    def sha256(self) -> str:
        payload = {
            "calendar_id": self.calendar_id,
            "domain": self.domain.value,
            "coverage_start": self.coverage_start.isoformat(),
            "source_as_of": self.source_as_of.isoformat(),
            "open_days": sorted(day.isoformat() for day in self.open_days),
            "source_ref": self.source_ref,
            "source_sha256": self.source_sha256,
            "recorded_at": self.recorded_at.astimezone(timezone.utc).isoformat(),
        }
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class NavPublication:
    """Observed publication timestamp for a scheme NAV, distinct from its business date."""

    scheme_id: str
    business_date: date
    published_at: datetime
    source_ref: str
    source_sha256: str

    def __post_init__(self) -> None:
        if not self.scheme_id.strip() or not self.source_ref.strip():
            raise TradingClockError("NAV scheme id and source reference are required")
        if self.published_at.tzinfo is None or self.published_at.utcoffset() is None:
            raise TradingClockError("NAV publication timestamp must be timezone aware")
        if self.published_at.astimezone(IST).date() < self.business_date:
            raise TradingClockError("NAV cannot be published before its business date")
        if len(self.source_sha256) != 64 or any(c not in "0123456789abcdef" for c in self.source_sha256):
            raise TradingClockError("source_sha256 must be lowercase SHA-256")


class TradingClock:
    """Unifies distinct Indian market, fund-scheme, bank and NAV publication clocks.

    All calendars are explicit, source-bounded inputs. Missing coverage raises instead of
    substituting weekdays, universal NAV cutoffs, or assumed publication times.
    """

    timezone = IST

    def __init__(self, exchange_calendar: VenueCalendar, *,
                 scheme_calendars: Mapping[str, BusinessCalendar],
                 bank_calendars: Mapping[str, BusinessCalendar],
                 nav_publications: tuple[NavPublication, ...] = ()) -> None:
        self.exchange_calendar = exchange_calendar
        self.scheme_calendars = MappingProxyType(dict(scheme_calendars))
        self.bank_calendars = MappingProxyType(dict(bank_calendars))
        self.nav_publications: dict[tuple[str, date], NavPublication] = {}
        for key, cal in self.scheme_calendars.items():
            if key != cal.calendar_id or cal.domain is not CalendarDomain.SCHEME:
                raise TradingClockError("scheme calendar mapping key/domain mismatch")
        for key, cal in self.bank_calendars.items():
            if key != cal.calendar_id or cal.domain is not CalendarDomain.BANK_SETTLEMENT:
                raise TradingClockError("bank calendar mapping key/domain mismatch")
        for publication in nav_publications:
            publication_key = (publication.scheme_id, publication.business_date)
            if publication_key in self.nav_publications:
                raise TradingClockError(f"duplicate NAV publication for {publication_key}")
            calendar = self.scheme_calendars.get(publication.scheme_id)
            if calendar is None or not calendar.is_open(publication.business_date):
                raise TradingClockError("NAV publication business date is not a sourced scheme day")
            self.nav_publications[publication_key] = publication

    def exchange_session(self, exchange: str, day: date) -> bool:
        try:
            return self.exchange_calendar.is_session(exchange, day)
        except CalendarError as exc:
            raise TradingClockError(str(exc)) from exc

    def exchange_bounds_utc(self, exchange: str, day: date) -> tuple[datetime, datetime]:
        try:
            return self.exchange_calendar.session_bounds_utc(exchange, day)
        except CalendarError as exc:
            raise TradingClockError(str(exc)) from exc

    def scheme_business_day(self, scheme_id: str, day: date) -> bool:
        calendar = self.scheme_calendars.get(scheme_id)
        if calendar is None:
            raise TradingClockError(f"unknown scheme calendar {scheme_id}")
        return calendar.is_open(day)

    def bank_settlement_day(self, bank_calendar_id: str, day: date) -> bool:
        calendar = self.bank_calendars.get(bank_calendar_id)
        if calendar is None:
            raise TradingClockError(f"unknown bank settlement calendar {bank_calendar_id}")
        return calendar.is_open(day)

    def nav_published_at(self, scheme_id: str, business_date: date) -> datetime:
        publication = self.nav_publications.get((scheme_id, business_date))
        if publication is None:
            raise TradingClockError(f"no sourced NAV publication time for {scheme_id} {business_date}")
        return publication.published_at.astimezone(IST)


__all__ = [
    "BusinessCalendar", "CalendarDomain", "NavPublication", "TradingClock",
    "TradingClockError",
]
