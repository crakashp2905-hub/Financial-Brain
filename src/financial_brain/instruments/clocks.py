"""Asset-specific clocks (FB-0104, FB-0105, FB-0109).

An exchange session, a scheme business day, a bank settlement day and a NAV publication time are four
different clocks. An ETF fills inside an exchange session; a mutual-fund request is dated by a scheme
business day and a cut-off; its NAV does not exist until it is published; money moves on bank days.

Two rules shape this module:

* **No calendar is assumed.** A ``Calendar`` covers a stated span and raises outside it, because a holiday
  list that has not been published yet cannot be guessed.
* **No cut-off or publication time is built in.** They are effective-dated rules with a named source, and a
  scheme's own rule (from its SID, KIM or an AMC notice) overrides its category's. With no rule the clock
  raises: a universal cut-off is not sufficient.

All wall-clock times are Asia/Kolkata. India has no daylight saving, so a fixed offset is exact.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from enum import Enum

from financial_brain.kernel.clock import ClockError, utc

IST = timezone(timedelta(hours=5, minutes=30), "IST")


def ist(value: datetime) -> datetime:
    return utc(value).astimezone(IST)


def at(day: date, wall: time) -> datetime:
    return datetime.combine(day, wall, tzinfo=IST)


@dataclass(frozen=True)
class Stamps:
    """The timestamps one observation carries. A strategy may use it only from ``available_at``.

    ``source_ts`` is the time the source puts on the fact; ``exchange_ts`` the exchange's own, when the feed
    has one; ``receipt_ts`` when it reached this machine; ``ingested_at`` when it was stored;
    ``published_at`` when the publisher released it (a NAV, a filing); ``available_at`` when a strategy
    could first read it.
    """

    source_ts: datetime
    receipt_ts: datetime
    ingested_at: datetime
    available_at: datetime
    exchange_ts: datetime | None = None
    published_at: datetime | None = None

    def __post_init__(self) -> None:
        for name in ("source_ts", "receipt_ts", "ingested_at", "available_at", "exchange_ts",
                     "published_at"):
            v = getattr(self, name)
            if v is not None:
                object.__setattr__(self, name, utc(v))
        if not (self.source_ts <= self.receipt_ts <= self.ingested_at <= self.available_at):
            raise ClockError("stamps out of order: require source <= receipt <= ingested <= available")
        if self.exchange_ts is not None and self.exchange_ts > self.receipt_ts:
            raise ClockError("an exchange timestamp after receipt is a clock fault")
        if self.published_at is not None and not (self.source_ts <= self.published_at <= self.available_at):
            raise ClockError("published_at must lie between the source time and availability")

    def usable_at(self, decision_at: datetime) -> bool:
        return self.available_at <= utc(decision_at)


class CalendarKind(str, Enum):
    EXCHANGE = "EXCHANGE"
    SCHEME = "SCHEME"
    BANK = "BANK"


@dataclass(frozen=True)
class Calendar:
    """Open days over a stated span. Outside the span every question raises."""

    name: str
    kind: CalendarKind
    covers_from: date
    covers_through: date
    holidays: frozenset[date]
    source: str
    special_sessions: frozenset[date] = frozenset()      # open days that fall on a weekend
    weekend: tuple[int, ...] = (5, 6)

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", CalendarKind(self.kind))
        object.__setattr__(self, "holidays", frozenset(self.holidays))
        object.__setattr__(self, "special_sessions", frozenset(self.special_sessions))
        if self.covers_through < self.covers_from:
            raise ClockError("calendar coverage ends before it starts")
        if not self.source:
            raise ClockError("a calendar needs a named source")
        if self.holidays & self.special_sessions:
            raise ClockError("a day cannot be both a holiday and a special session")

    @classmethod
    def from_observed(cls, name: str, kind: CalendarKind, sessions: list[date], source: str) -> Calendar:
        """A historical calendar from the days that actually had a session.

        Weekdays without a session inside the span are holidays; weekend days with one are special
        sessions. It covers only the observed span and says nothing about the future.
        """
        if not sessions:
            raise ClockError("no sessions observed")
        seen = set(sessions)
        lo, hi = min(seen), max(seen)
        days = [lo + timedelta(n) for n in range((hi - lo).days + 1)]
        return cls(name, kind, lo, hi,
                   holidays=frozenset(d for d in days if d.weekday() < 5 and d not in seen),
                   special_sessions=frozenset(d for d in seen if d.weekday() >= 5), source=source)

    def _covered(self, day: date) -> None:
        if not (self.covers_from <= day <= self.covers_through):
            raise ClockError(f"{self.name} calendar does not cover {day.isoformat()} "
                             f"(covers {self.covers_from.isoformat()} to {self.covers_through.isoformat()})")

    def is_open(self, day: date) -> bool:
        self._covered(day)
        if day in self.special_sessions:
            return True
        return day.weekday() not in self.weekend and day not in self.holidays

    def on_or_after(self, day: date) -> date:
        while not self.is_open(day):
            day += timedelta(1)
        return day

    def next_open(self, day: date) -> date:
        return self.on_or_after(day + timedelta(1))

    def previous_open(self, day: date) -> date:
        day -= timedelta(1)
        while not self.is_open(day):
            day -= timedelta(1)
        return day

    def shift(self, day: date, n: int) -> date:
        """``n`` open days after an open day (n >= 0)."""
        if n < 0:
            raise ClockError("shift takes a non-negative count")
        if not self.is_open(day):
            raise ClockError(f"{day.isoformat()} is not an open day on the {self.name} calendar")
        for _ in range(n):
            day = self.next_open(day)
        return day


@dataclass(frozen=True)
class SessionHours:
    effective_from: date
    open: time
    close: time
    source: str

    def __post_init__(self) -> None:
        if self.close <= self.open:
            raise ClockError("a session must close after it opens")


@dataclass(frozen=True)
class _Scoped:
    """A rule for one scheme (``scheme_uid``) or for a category of schemes, never both."""

    effective_from: date
    source: str
    category: str | None = None
    scheme_uid: str | None = None

    def _check_scope(self) -> None:
        if (self.category is None) == (self.scheme_uid is None):
            raise ClockError("a rule is scoped to exactly one of a category or a scheme")
        if not self.source:
            raise ClockError("a rule needs a named source")


@dataclass(frozen=True)
class CutoffRule(_Scoped):
    """Request cut-offs on a scheme business day. A request stamped before the cut-off is that day's."""

    purchase: time = field(default=time(0))
    redemption: time = field(default=time(0))

    def __post_init__(self) -> None:
        self._check_scope()


@dataclass(frozen=True)
class NavPublication(_Scoped):
    """When the NAV dated ``nav_date`` is published: ``day_offset`` scheme days later, at ``at``."""

    at: time = field(default=time(0))
    day_offset: int = 0

    def __post_init__(self) -> None:
        self._check_scope()
        if self.day_offset < 0:
            raise ClockError("a NAV cannot be published before its own date")


def _pick(rules: list, on: date, scheme_uid: str | None, category: str | None, what: str):
    for scope, key in (("scheme_uid", scheme_uid), ("category", category)):
        if key is None:
            continue
        hits = [r for r in rules if getattr(r, scope) == key and r.effective_from <= on]
        if hits:
            return max(hits, key=lambda r: r.effective_from)
    raise ClockError(f"no {what} rule for scheme {scheme_uid!r} / category {category!r} effective on "
                     f"{on.isoformat()}; a universal rule is not assumed")


@dataclass(frozen=True)
class TradingClock:
    exchange: Calendar
    scheme: Calendar
    bank: Calendar
    hours: tuple[SessionHours, ...] = ()
    cutoffs: tuple[CutoffRule, ...] = ()
    publications: tuple[NavPublication, ...] = ()

    def calendar(self, kind: CalendarKind) -> Calendar:
        return {CalendarKind.EXCHANGE: self.exchange, CalendarKind.SCHEME: self.scheme,
                CalendarKind.BANK: self.bank}[CalendarKind(kind)]

    # ---------------------------------------------------------------- exchange
    def session(self, day: date) -> tuple[datetime, datetime]:
        if not self.exchange.is_open(day):
            raise ClockError(f"{day.isoformat()} is not an exchange session")
        hits = [h for h in self.hours if h.effective_from <= day]
        if not hits:
            raise ClockError(f"no session hours effective on {day.isoformat()}")
        h = max(hits, key=lambda x: x.effective_from)
        return at(day, h.open), at(day, h.close)

    def in_session(self, ts: datetime) -> bool:
        local = ist(ts)
        if not self.exchange.is_open(local.date()):
            return False
        opened, closed = self.session(local.date())
        return opened <= local < closed

    def settlement_day(self, trade_day: date, lag: int,
                       kind: CalendarKind = CalendarKind.EXCHANGE) -> date:
        return self.calendar(kind).shift(trade_day, lag)

    # ------------------------------------------------------------ mutual funds
    def cutoff(self, side: str, day: date, *, scheme_uid: str | None = None,
               category: str | None = None) -> datetime:
        if side not in ("purchase", "redemption"):
            raise ClockError("side is purchase or redemption")
        if not self.scheme.is_open(day):
            raise ClockError(f"{day.isoformat()} is not a scheme business day")
        rule = _pick(list(self.cutoffs), day, scheme_uid, category, "cut-off")
        return at(day, getattr(rule, side))

    def request_day(self, side: str, stamped: datetime, *, scheme_uid: str | None = None,
                    category: str | None = None) -> date:
        """The scheme business day a request time-stamped at ``stamped`` belongs to.

        Strictly before the cut-off on a business day is that day; at or after it, or on a non-business
        day, is the next one. This dates the *request* only. The applicable NAV also depends on when the
        funds are available to the scheme, which this clock does not know.
        """
        local = ist(stamped)
        day = local.date()
        if self.scheme.is_open(day) and local < self.cutoff(side, day, scheme_uid=scheme_uid,
                                                           category=category):
            return day
        return self.scheme.next_open(day)

    def nav_published_at(self, nav_date: date, *, scheme_uid: str | None = None,
                         category: str | None = None) -> datetime:
        if not self.scheme.is_open(nav_date):
            raise ClockError(f"no NAV is struck for {nav_date.isoformat()}: not a scheme business day")
        rule = _pick(list(self.publications), nav_date, scheme_uid, category, "NAV publication")
        return at(self.scheme.shift(nav_date, rule.day_offset), rule.at)

    def nav_known(self, nav_date: date, when: datetime, *, scheme_uid: str | None = None,
                  category: str | None = None) -> bool:
        """Whether the NAV for ``nav_date`` could have been read at ``when``."""
        return ist(when) >= self.nav_published_at(nav_date, scheme_uid=scheme_uid, category=category)
