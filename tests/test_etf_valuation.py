from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
import duckdb

from financial_brain.etf_valuation import compare_latest_for_quote, compare_quote_to_inav
from financial_brain.forward_edge.market_events import EventKind, MarketEvent

IST = ZoneInfo("Asia/Kolkata")


def _quote(at: datetime) -> MarketEvent:
    return MarketEvent(
        event_id="q1", kind=EventKind.QUOTE, venue="NSE", instrument_id="NSE:ETFONE",
        event_time=at, received_at=at + timedelta(seconds=1),
        available_at=at + timedelta(seconds=1), source="KITE_CONNECT_READ_ONLY",
        payload={"bid": 100, "ask": 101, "last_price": 100.5, "volume": 1000},
    )


def _inav(at: datetime) -> dict:
    return {"indicative_nav": Decimal("100"), "indicative_nav_at": at,
            "source_published_at": at - timedelta(minutes=1),
            "source_url": "https://amc.example.in/etf/inav.csv",
            "source_document_sha256": "b" * 64}


def test_comparison_uses_synchronized_midpoint_and_returns_percent() -> None:
    quote_time = datetime(2026, 11, 23, 10, 0, tzinfo=IST)
    inav_time = quote_time + timedelta(seconds=1)
    result = compare_quote_to_inav(
        _quote(quote_time), _inav(inav_time),
        decision_time=quote_time + timedelta(seconds=2),
    )
    assert result.status == "COMPARABLE"
    assert result.market_mid == Decimal("100.5")
    assert result.premium_discount_pct == Decimal("0.500")
    assert result.timestamp_skew == timedelta(seconds=1)


@pytest.mark.parametrize(
    ("decision_delta", "quote_delta", "inav_delta", "wide_age", "expected"),
    [
        (timedelta(seconds=12), timedelta(0), timedelta(0), False, "STALE_QUOTE"),
        (timedelta(seconds=12), timedelta(seconds=11), timedelta(0), False, "STALE_INAV"),
        (timedelta(seconds=12), timedelta(0), timedelta(seconds=10), True,
         "ASYNCHRONOUS_OBSERVATIONS"),
    ],
)
def test_asynchronous_or_stale_inputs_are_refused(
    decision_delta, quote_delta, inav_delta, wide_age, expected
) -> None:
    start = datetime(2026, 11, 23, 10, 0, tzinfo=IST)
    kwargs = {"max_timestamp_skew": timedelta(seconds=2)}
    if wide_age:
        kwargs.update(max_quote_age=timedelta(seconds=20), max_inav_age=timedelta(seconds=20))
    result = compare_quote_to_inav(_quote(start + quote_delta), _inav(start + inav_delta),
                                   decision_time=start + decision_delta, **kwargs)
    assert result.status == "REFUSED"
    assert result.premium_discount_pct is None
    assert result.reason == expected


def test_comparison_refuses_unavailable_or_unprovenanced_inav() -> None:
    start = datetime(2026, 11, 23, 10, 0, tzinfo=IST)
    inav = _inav(start)
    inav["source_url"] = ""
    result = compare_quote_to_inav(
        _quote(start), inav, decision_time=start + timedelta(seconds=2))
    assert result.reason == "INAV_PROVENANCE_MISSING"
    assert result.premium_discount_pct is None
    inav = _inav(start)
    inav["source_document_sha256"] = "bad"
    result = compare_quote_to_inav(
        _quote(start), inav, decision_time=start + timedelta(seconds=2))
    assert result.reason == "INAV_PROVENANCE_MISSING"
    with pytest.raises(ValueError, match="timezone-aware"):
        compare_quote_to_inav(_quote(start), _inav(start), decision_time=datetime(2026, 11, 23))


def test_latest_database_inav_is_resolved_asof_before_comparison() -> None:
    con = duckdb.connect(":memory:")
    con.execute("""CREATE TABLE etf_reference_observations
        (observed_on DATE, exchange VARCHAR, symbol VARCHAR, isin VARCHAR)""")
    con.execute("""CREATE TABLE etf_product_disclosures
        (isin VARCHAR, observed_on DATE, indicative_nav DECIMAL(20,8),
         indicative_nav_at TIMESTAMPTZ, source_published_at TIMESTAMPTZ, source_url VARCHAR,
         source_document_sha256 VARCHAR)""")
    con.execute("""INSERT INTO etf_reference_observations VALUES
        ('2026-11-23','NSE','ETFONE','INF000A01010')""")
    con.execute("""INSERT INTO etf_product_disclosures VALUES
        ('INF000A01010','2026-11-23',100,'2026-11-23 10:00:00+05:30',
         '2026-11-23 09:59:00+05:30','https://amc.example.in/inav','%s'),
        ('INF000A01010','2026-11-23',110,'2026-11-23 11:00:00+05:30',
         '2026-11-23 10:59:00+05:30','https://amc.example.in/inav','%s')"""
        % ("c" * 64, "d" * 64))
    quote_time = datetime(2026, 11, 23, 10, 0, tzinfo=IST)
    try:
        result = compare_latest_for_quote(
            con, _quote(quote_time), decision_time=quote_time + timedelta(seconds=2))
    finally:
        con.close()
    assert result.status == "COMPARABLE"
    assert result.indicative_nav == Decimal("100.00000000")
