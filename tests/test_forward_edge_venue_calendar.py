"""FE-0034 venue calendar is evidence-backed, never a weekday heuristic."""
from __future__ import annotations

from datetime import date, datetime, time, timezone

import pytest

from financial_brain.forward_edge.venue_calendar import CalendarError, Session, VenueCalendar


def _session(venue: str, day: date, kind: str = "NORMAL") -> Session:
    return Session(venue, day, time(9, 15), time(15, 30), kind, "official:fixture",
                   "a" * 64, datetime(2026, 11, 1, tzinfo=timezone.utc))


def test_next_session_uses_sourced_records_and_includes_special_sessions() -> None:
    sessions = [_session("NSE", date(2026, 11, 13)),
                _session("NSE", date(2026, 11, 15), "SPECIAL_MUHURAT"),
                _session("BSE", date(2026, 11, 16))]
    calendar = VenueCalendar(sessions, source_as_of=date(2026, 11, 16))
    assert calendar.next_session("NSE", date(2026, 11, 13)).session_type == "SPECIAL_MUHURAT"
    assert calendar.next_session("BSE", date(2026, 11, 13)).day == date(2026, 11, 16)
    assert calendar.is_session("NSE", date(2026, 11, 15))
    assert len(calendar.sha256()) == 64


def test_missing_calendar_coverage_and_future_horizon_fail_closed() -> None:
    calendar = VenueCalendar([_session("NSE", date(2026, 11, 13))], source_as_of=date(2026, 11, 13))
    with pytest.raises(CalendarError, match="no sourced"):
        calendar.next_session("NSE", date(2026, 11, 13))
    with pytest.raises(CalendarError, match="beyond"):
        calendar.is_session("NSE", date(2026, 11, 14))
    with pytest.raises(CalendarError, match="exceeds"):
        calendar.next_session("NSE", date(2026, 11, 12), through=date(2026, 11, 14))


def test_bad_sources_times_venues_and_duplicate_session_records_are_rejected() -> None:
    with pytest.raises(CalendarError, match="source_sha256"):
        Session("NSE", date(2026, 11, 13), time(9), time(10), "NORMAL", "official", "bad",
                datetime(2026, 11, 1, tzinfo=timezone.utc))
    with pytest.raises(CalendarError, match="timezone aware"):
        Session("NSE", date(2026, 11, 13), time(9), time(10), "NORMAL", "official", "a" * 64,
                datetime(2026, 11, 1))
    with pytest.raises(CalendarError, match="duplicate"):
        VenueCalendar([_session("NSE", date(2026, 11, 13)), _session("NSE", date(2026, 11, 13))],
                      source_as_of=date(2026, 11, 13))
