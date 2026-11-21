from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import pytest

from financial_brain.kernel.fund_transactions import (
    FundTransactionError,
    SchemeTransactionCalendar,
    SchemeTransactionRule,
    TransactionKind,
)
from financial_brain.kernel.trading_clock import BusinessCalendar, CalendarDomain

HASH = "c" * 64
PUBLISHED = datetime(2026, 11, 1, 10, tzinfo=timezone.utc)
OPEN_DAYS = frozenset({
    date(2026, 11, 18), date(2026, 11, 19), date(2026, 11, 20),
    date(2026, 11, 23), date(2026, 11, 24), date(2026, 11, 25),
    date(2026, 11, 26), date(2026, 11, 27), date(2026, 11, 30),
})


def _calendar(scheme_id: str) -> BusinessCalendar:
    return BusinessCalendar(
        calendar_id=scheme_id, domain=CalendarDomain.SCHEME,
        coverage_start=date(2026, 11, 18), source_as_of=date(2026, 11, 30),
        open_days=OPEN_DAYS, source_ref=f"{scheme_id}-business-days", source_sha256=HASH,
        recorded_at=PUBLISHED,
    )


def _rule(scheme_id: str, kind: TransactionKind, cutoff: time) -> SchemeTransactionRule:
    return SchemeTransactionRule(
        scheme_id=scheme_id, transaction_kind=kind,
        effective_from=date(2026, 11, 1), effective_to=None, cutoff_local=cutoff,
        before_cutoff_lag=0, after_cutoff_lag=1, closed_day_lag=0,
        published_at=PUBLISHED, source_ref=f"{scheme_id}-sid-kim-amc-notice",
        source_sha256=HASH,
    )


def _at_ist(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute,
                    tzinfo=timezone(timedelta(hours=5, minutes=30)))


def test_fund_rule_assigns_before_and_after_cutoff_on_its_scheme_calendar() -> None:
    scheme = _calendar("FUND-A")
    engine = SchemeTransactionCalendar(
        {scheme.calendar_id: scheme}, (_rule("FUND-A", TransactionKind.PURCHASE, time(15)),))
    assert engine.assign_nav_date("FUND-A", TransactionKind.PURCHASE,
                                  received_at=_at_ist(date(2026, 11, 20), 14, 59)) == date(2026, 11, 20)
    assert engine.assign_nav_date("FUND-A", TransactionKind.PURCHASE,
                                  received_at=_at_ist(date(2026, 11, 20), 15, 1)) == date(2026, 11, 23)


def test_each_fund_and_transaction_kind_can_have_distinct_cutoff() -> None:
    a, b = _calendar("FUND-A"), _calendar("FUND-B")
    engine = SchemeTransactionCalendar(
        {a.calendar_id: a, b.calendar_id: b},
        (_rule("FUND-A", TransactionKind.PURCHASE, time(15)),
         _rule("FUND-B", TransactionKind.PURCHASE, time(13)),
         _rule("FUND-B", TransactionKind.REDEMPTION, time(16))),
    )
    when = _at_ist(date(2026, 11, 20), 14)
    assert engine.assign_nav_date("FUND-A", TransactionKind.PURCHASE, received_at=when) == date(2026, 11, 20)
    assert engine.assign_nav_date("FUND-B", TransactionKind.PURCHASE, received_at=when) == date(2026, 11, 23)
    assert engine.assign_nav_date("FUND-B", TransactionKind.REDEMPTION, received_at=when) == date(2026, 11, 20)


def test_closed_scheme_day_uses_sourced_calendar_not_weekday_guessing() -> None:
    scheme = _calendar("FUND-A")
    engine = SchemeTransactionCalendar(
        {scheme.calendar_id: scheme}, (_rule("FUND-A", TransactionKind.PURCHASE, time(15)),))
    assert engine.assign_nav_date("FUND-A", TransactionKind.PURCHASE,
                                  received_at=_at_ist(date(2026, 11, 21), 10)) == date(2026, 11, 23)


def test_future_published_missing_or_overlapping_rules_fail_closed() -> None:
    scheme = _calendar("FUND-A")
    late_notice = _rule("FUND-A", TransactionKind.PURCHASE, time(15))
    late_notice = SchemeTransactionRule(**(late_notice.__dict__ | {
        "published_at": datetime(2026, 11, 20, 10, tzinfo=timezone.utc),
    }))
    engine = SchemeTransactionCalendar({scheme.calendar_id: scheme}, (late_notice,))
    with pytest.raises(FundTransactionError, match="no unique published transaction rule"):
        engine.assign_nav_date("FUND-A", TransactionKind.PURCHASE,
                               received_at=_at_ist(date(2026, 11, 19), 12))

    with pytest.raises(FundTransactionError, match="no unique published transaction rule"):
        SchemeTransactionCalendar({scheme.calendar_id: scheme}, ()).assign_nav_date(
            "FUND-A", TransactionKind.PURCHASE, received_at=_at_ist(date(2026, 11, 20), 12))

    first = _rule("FUND-A", TransactionKind.PURCHASE, time(15))
    overlap = SchemeTransactionRule(**(first.__dict__ | {
        "effective_from": date(2026, 11, 15), "cutoff_local": time(14),
    }))
    with pytest.raises(FundTransactionError, match="overlapping effective rules"):
        SchemeTransactionCalendar({scheme.calendar_id: scheme}, (first, overlap))


def test_unknown_scheme_or_naive_receipt_time_is_refused() -> None:
    scheme = _calendar("FUND-A")
    engine = SchemeTransactionCalendar(
        {scheme.calendar_id: scheme}, (_rule("FUND-A", TransactionKind.PURCHASE, time(15)),))
    with pytest.raises(FundTransactionError, match="unknown scheme"):
        engine.assign_nav_date("FUND-X", TransactionKind.PURCHASE,
                               received_at=_at_ist(date(2026, 11, 20), 12))
    with pytest.raises(FundTransactionError, match="timezone aware"):
        engine.assign_nav_date("FUND-A", TransactionKind.PURCHASE,
                               received_at=datetime(2026, 11, 20, 12))
