"""Time-valid instants (FB-0043, FB-0070 ordering).

Every market fact carries four instants. The ordering below is an invariant of the platform: a decision may only
use information that was available at or before it was taken, and an event cannot be ingested before it happened
or be available before it was ingested.

    event_time <= ingest_time <= available_time <= decision_time
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from financial_brain.kernel.closures_n import MarketCalendar


class ClockError(ValueError):
    """Naive timestamps, out-of-order instants and future-dated facts are rejected."""


def utc(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise ClockError("expected a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ClockError("naive timestamps are not allowed; attach a timezone")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class Instants:
    event_time: datetime
    ingest_time: datetime
    available_time: datetime
    decision_time: datetime

    def __post_init__(self) -> None:
        for name in ("event_time", "ingest_time", "available_time", "decision_time"):
            object.__setattr__(self, name, utc(getattr(self, name)))
        if not (self.event_time <= self.ingest_time <= self.available_time <= self.decision_time):
            raise ClockError(
                "instants out of order: require event <= ingest <= available <= decision"
            )


@dataclass(frozen=True)
class MarketFactTimes:
    """Timestamp provenance required to determine when a market fact was usable."""

    received_at: datetime
    available_at: datetime
    ingested_at: datetime
    source_at: datetime | None = None
    exchange_at: datetime | None = None
    published_at: datetime | None = None

    def __post_init__(self) -> None:
        for name in (
            "received_at",
            "available_at",
            "ingested_at",
            "source_at",
            "exchange_at",
            "published_at",
        ):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, utc(value))
        if self.ingested_at < self.received_at:
            raise ClockError("ingestion cannot precede receipt")
        if self.available_at < self.received_at:
            raise ClockError("strategy availability cannot precede receipt")
        if self.published_at is not None and self.available_at < self.published_at:
            raise ClockError("strategy availability cannot precede publication")

    def assert_usable_at(self, decision_time: datetime) -> None:
        """Reject an observation not yet available or published at decision time."""
        decision = utc(decision_time)
        if self.available_at > decision:
            raise ClockError("market fact was not yet available to the strategy")


def assert_not_future(instant: datetime, now: datetime) -> datetime:
    """A fact stamped after the reference clock cannot exist yet; refuse it."""
    t, n = utc(instant), utc(now)
    if t > n:
        raise ClockError(f"timestamp {t.isoformat()} is after the reference clock {n.isoformat()}")
    return t


@dataclass(frozen=True)
class TradingClock:
    """Separate exchange, fund-scheme and bank business-time domains.

    Calendars are supplied from reviewed source data; this type intentionally does
    not invent holiday dates or treat one scheme's calendar as another's.
    """

    exchange_calendar: MarketCalendar = field(default_factory=MarketCalendar)
    bank_settlement_calendar: MarketCalendar = field(default_factory=MarketCalendar)
    scheme_calendars: dict[str, MarketCalendar] = field(default_factory=dict)
    exchange_hours: dict[str, tuple[time, time]] = field(default_factory=dict)
    timezone_name: str = "Asia/Kolkata"

    def __post_init__(self) -> None:
        try:
            ZoneInfo(self.timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise ClockError(f"unknown IANA timezone {self.timezone_name!r}") from exc
        for venue, (opens, closes) in self.exchange_hours.items():
            if not venue or opens >= closes:
                raise ClockError("exchange hours need a venue and open time before close time")
        if any(not scheme for scheme in self.scheme_calendars):
            raise ClockError("scheme calendar keys must be non-empty identifiers")

    @property
    def timezone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone_name)

    def is_exchange_session(self, day: date) -> bool:
        return self.exchange_calendar.is_session(day)

    def is_bank_settlement_day(self, day: date) -> bool:
        return self.bank_settlement_calendar.is_session(day)

    def is_scheme_business_day(self, scheme_id: str, day: date) -> bool:
        calendar = self.scheme_calendars.get(scheme_id)
        if calendar is None:
            raise ClockError(f"no reviewed business calendar for scheme {scheme_id!r}")
        return calendar.is_session(day)

    def nav_publication_at(self, scheme_id: str, day: date, publication_time: time) -> datetime:
        """Return an aware scheme publication instant; unknown schemes are refused."""
        if scheme_id not in self.scheme_calendars:
            raise ClockError(f"no reviewed business calendar for scheme {scheme_id!r}")
        return datetime.combine(day, publication_time, tzinfo=self.timezone)

    def exchange_is_open(self, venue: str, instant: datetime) -> bool:
        """Check a timestamp against configured local session hours and holiday calendar."""
        if venue not in self.exchange_hours:
            raise ClockError(f"no session hours configured for venue {venue!r}")
        local = utc(instant).astimezone(self.timezone)
        opens, closes = self.exchange_hours[venue]
        return self.is_exchange_session(local.date()) and opens <= local.time().replace(tzinfo=None) < closes
