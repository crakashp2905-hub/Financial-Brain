from __future__ import annotations

from datetime import date, datetime, time, timezone

import pytest

from financial_brain.forward_edge.venue_calendar import Session, VenueCalendar
from financial_brain.kernel.trading_clock import (
    BusinessCalendar,
    CalendarDomain,
    NavPublication,
    TradingClock,
    TradingClockError,
)

HASH = "a" * 64
NOW = datetime(2026, 11, 21, 12, tzinfo=timezone.utc)


def _exchange_calendar() -> VenueCalendar:
    return VenueCalendar((Session(
        venue="NSE", day=date(2026, 11, 20), opens=time(9, 15), closes=time(15, 30),
        session_type="REGULAR", source_ref="nse-calendar.csv", source_sha256=HASH,
        recorded_at=NOW,
    ),), source_as_of=date(2026, 11, 23))


def _business_calendar(calendar_id: str, domain: CalendarDomain) -> BusinessCalendar:
    return BusinessCalendar(
        calendar_id=calendar_id, domain=domain,
        coverage_start=date(2026, 11, 19), source_as_of=date(2026, 11, 23),
        open_days=frozenset({date(2026, 11, 19), date(2026, 11, 20), date(2026, 11, 23)}),
        source_ref=f"{calendar_id}.csv", source_sha256=HASH, recorded_at=NOW,
    )


def test_trading_clock_keeps_exchange_scheme_bank_and_nav_times_distinct() -> None:
    scheme = _business_calendar("SCHEME-A", CalendarDomain.SCHEME)
    bank = _business_calendar("BANK-IN", CalendarDomain.BANK_SETTLEMENT)
    published = datetime(2026, 11, 20, 22, 5, tzinfo=timezone.utc)
    clock = TradingClock(
        _exchange_calendar(), scheme_calendars={scheme.calendar_id: scheme},
        bank_calendars={bank.calendar_id: bank},
        nav_publications=(NavPublication("SCHEME-A", date(2026, 11, 20), published,
                                         "amfi-nav.txt", HASH),),
    )

    assert clock.timezone.key == "Asia/Kolkata"
    assert clock.exchange_session("NSE", date(2026, 11, 20))
    assert not clock.exchange_session("NSE", date(2026, 11, 21))
    assert clock.scheme_business_day("SCHEME-A", date(2026, 11, 20))
    assert clock.bank_settlement_day("BANK-IN", date(2026, 11, 20))
    assert clock.nav_published_at("SCHEME-A", date(2026, 11, 20)).hour == 3
    assert clock.exchange_bounds_utc("NSE", date(2026, 11, 20))[0].hour == 3


def test_business_calendar_fails_closed_outside_source_coverage() -> None:
    calendar = _business_calendar("BANK-IN", CalendarDomain.BANK_SETTLEMENT)
    with pytest.raises(TradingClockError, match="no coverage"):
        calendar.is_open(date(2026, 11, 24))


def test_business_calendar_never_guesses_weekends_or_holidays() -> None:
    calendar = _business_calendar("BANK-IN", CalendarDomain.BANK_SETTLEMENT)
    assert not calendar.is_open(date(2026, 11, 21))
    assert calendar.next_open(date(2026, 11, 20)) == date(2026, 11, 23)


def test_nav_publication_requires_actual_aware_publication_after_business_date() -> None:
    with pytest.raises(TradingClockError, match="timezone aware"):
        NavPublication("SCHEME-A", date(2026, 11, 20), datetime(2026, 11, 20, 15, 0),
                       "amfi", HASH)
    with pytest.raises(TradingClockError, match="before its business date"):
        NavPublication("SCHEME-A", date(2026, 11, 20),
                       datetime(2026, 11, 19, 17, 0, tzinfo=timezone.utc), "amfi", HASH)


def test_missing_nav_publication_time_is_not_inferred() -> None:
    scheme = _business_calendar("SCHEME-A", CalendarDomain.SCHEME)
    clock = TradingClock(_exchange_calendar(), scheme_calendars={scheme.calendar_id: scheme},
                         bank_calendars={})
    with pytest.raises(TradingClockError, match="no sourced NAV publication"):
        clock.nav_published_at("SCHEME-A", date(2026, 11, 20))
