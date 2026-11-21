from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from financial_brain.forward_edge.market_data import (
    KiteQuoteNormalizationError,
    executable_quote,
    normalize_kite_quote,
)
from financial_brain.forward_edge.clock import FixedClock
from financial_brain.forward_edge.market_events import EventKind


def quote():
    return {"timestamp": "2026-11-13 10:15:00", "last_trade_time": "2026-11-13 10:14:59",
            "last_price": 101.5, "volume": 1000,
            "ohlc": {"open": 100.0, "high": 102.0, "low": 99.0, "close": 98.5},
            "depth": {"buy": [{"price": 101.4, "quantity": 12}],
                      "sell": [{"price": 101.6, "quantity": 9}]}}


def test_kite_quote_event_preserves_exchange_and_receipt_clocks_and_source():
    received = datetime(2026, 11, 13, 4, 45, 1, tzinfo=timezone.utc)
    event = normalize_kite_quote("NSE:INFY", quote(), received_at=received)
    assert event.kind is EventKind.QUOTE
    assert event.venue == "NSE"
    assert event.instrument_id == "NSE:INFY"
    assert event.event_time.isoformat() == "2026-11-13T04:45:00+00:00"
    assert event.received_at == received
    assert event.available_at == received
    assert event.source == "KITE_CONNECT_READ_ONLY"
    assert event.payload["bid"] == 101.4 and event.payload["ask"] == 101.6
    assert event.payload["session_high"] == 102.0 and event.payload["session_low"] == 99.0
    assert event.payload["previous_close"] == 98.5


def test_invalid_previous_close_is_rejected_instead_of_becoming_gap_strategy_input():
    with pytest.raises(KiteQuoteNormalizationError, match="OHLC close must be positive and finite"):
        normalize_kite_quote("NSE:INFY", quote() | {"ohlc": quote()["ohlc"] | {"close": float("nan")}},
                             received_at=datetime.now(timezone.utc))


def test_missing_exchange_clock_is_refused():
    with pytest.raises(KiteQuoteNormalizationError, match="timestamp is missing"):
        normalize_kite_quote("NSE:INFY", quote() | {"timestamp": None},
                             received_at=datetime.now(timezone.utc))


def test_naive_kite_exchange_timestamp_uses_documented_ist_clock_not_receipt_clock():
    received = datetime(2026, 11, 13, 4, 45, 1, tzinfo=timezone.utc)
    event = normalize_kite_quote("NSE:INFY", quote(), received_at=received)
    assert event.event_time == datetime(2026, 11, 13, 4, 45, tzinfo=timezone.utc)
    assert event.event_time != received


def test_exchange_clock_ahead_of_receipt_beyond_kite_skew_tolerance_is_rejected():
    received = datetime(2026, 11, 13, 4, 45, 0, tzinfo=timezone.utc)
    future = quote() | {"timestamp": "2026-11-13 10:15:03"}
    with pytest.raises(KiteQuoteNormalizationError, match="future-skew tolerance"):
        normalize_kite_quote("NSE:INFY", future, received_at=received)

    # A caller can declare the source's measured clock allowance explicitly.
    accepted = normalize_kite_quote("NSE:INFY", future, received_at=received,
                                    max_future_skew=timedelta(seconds=3))
    assert accepted.event_time == datetime(2026, 11, 13, 4, 45, 3, tzinfo=timezone.utc)


def test_stale_exchange_observation_is_preserved_for_audit_not_retimed():
    received = datetime(2026, 11, 13, 5, 0, tzinfo=timezone.utc)
    stale = quote() | {"timestamp": "2026-11-13 10:15:00"}
    event = normalize_kite_quote("NSE:INFY", stale, received_at=received)
    assert event.event_time == datetime(2026, 11, 13, 4, 45, tzinfo=timezone.utc)
    assert event.received_at == received
    assert event.event_time < event.received_at


def test_negative_source_clock_tolerances_are_rejected():
    received = datetime(2026, 11, 13, 4, 45, 1, tzinfo=timezone.utc)
    with pytest.raises(KiteQuoteNormalizationError, match="availability delay"):
        normalize_kite_quote("NSE:INFY", quote(), received_at=received,
                             availability_delay=-timedelta(milliseconds=1))
    with pytest.raises(KiteQuoteNormalizationError, match="future skew"):
        normalize_kite_quote("NSE:INFY", quote(), received_at=received,
                             max_future_skew=-timedelta(milliseconds=1))


def test_ltp_only_observation_is_archived_as_non_executable_tick():
    event = normalize_kite_quote("NSE:INFY", quote() | {"depth": None},
                                 received_at=datetime.now(timezone.utc))
    assert event.kind is EventKind.TICK
    assert event.payload["price"] == 101.5
    assert event.payload["non_executable_reason"] == "TWO_SIDED_DEPTH_UNAVAILABLE"
    assert "bid" not in event.payload and "ask" not in event.payload
    assert executable_quote(event) is None


def test_executable_quote_requires_two_sided_positive_displayed_liquidity():
    received = datetime.now(timezone.utc)
    event = normalize_kite_quote("NSE:INFY", quote(), received_at=received)
    execution = executable_quote(event)
    assert execution is not None
    assert execution["bid"] == Decimal("101.4") and execution["ask"] == Decimal("101.6")
    assert execution["bid_quantity"] == 12 and execution["ask_quantity"] == 9

    no_liquidity = quote() | {"depth": {"buy": [{"price": 101.4, "quantity": 0}],
                                      "sell": [{"price": 101.6, "quantity": 0}]}}
    assert executable_quote(normalize_kite_quote("NSE:INFY", no_liquidity,
                                                  received_at=received)) is None


def test_locked_or_crossed_books_are_not_executable():
    received = datetime(2026, 11, 13, 4, 45, 1, tzinfo=timezone.utc)
    locked = quote() | {"depth": {
        "buy": [{"price": 101.6, "quantity": 12}],
        "sell": [{"price": 101.6, "quantity": 9}],
    }}
    locked_event = normalize_kite_quote("NSE:INFY", locked, received_at=received)
    assert executable_quote(locked_event) is None

    crossed = quote() | {"depth": {
        "buy": [{"price": 101.7, "quantity": 12}],
        "sell": [{"price": 101.6, "quantity": 9}],
    }}
    with pytest.raises(ValueError, match="bid cannot exceed ask"):
        normalize_kite_quote("NSE:INFY", crossed, received_at=received)


def test_circuit_limit_touch_and_non_continuous_status_are_not_executable():
    received = datetime(2026, 11, 13, 4, 45, 1, tzinfo=timezone.utc)
    base = normalize_kite_quote("NSE:INFY", quote(), received_at=received)
    assert executable_quote(base) is not None

    at_lower = normalize_kite_quote("NSE:INFY", quote() | {"lower_circuit_limit": 101.4},
                                    received_at=received)
    at_upper = normalize_kite_quote("NSE:INFY", quote() | {"upper_circuit_limit": 101.6},
                                    received_at=received)
    assert executable_quote(at_lower) is None
    assert executable_quote(at_upper) is None

    auction = normalize_kite_quote("NSE:INFY", quote() | {"exchange_status": "AUCTION"},
                                   received_at=received)
    assert executable_quote(auction) is None


def test_unsorted_or_malformed_depth_is_not_executable():
    received = datetime(2026, 11, 13, 4, 45, 1, tzinfo=timezone.utc)
    unsorted = quote() | {"depth": {
        "buy": [{"price": 101.4, "quantity": 12}, {"price": 101.5, "quantity": 2}],
        "sell": [{"price": 101.6, "quantity": 9}],
    }}
    assert executable_quote(normalize_kite_quote("NSE:INFY", unsorted,
                                                  received_at=received)) is None


def test_naive_receipt_clock_is_rejected_instead_of_claimed_as_observed_time():
    with pytest.raises(KiteQuoteNormalizationError, match="timezone aware"):
        normalize_kite_quote("NSE:INFY", quote(), received_at=datetime(2026, 11, 13, 10, 15))


def test_repeated_exchange_snapshot_at_a_later_receipt_keeps_both_observations():
    first = normalize_kite_quote("NSE:INFY", quote(),
                                 received_at=datetime(2026, 11, 13, 4, 45, 1, tzinfo=timezone.utc))
    second = normalize_kite_quote("NSE:INFY", quote(),
                                  received_at=datetime(2026, 11, 13, 4, 45, 2, tzinfo=timezone.utc))
    assert first.event_time == second.event_time
    assert first.event_id != second.event_id


def test_injected_clock_supplies_receipt_and_explicit_availability_delay():
    received = datetime(2026, 11, 13, 4, 45, 1, tzinfo=timezone.utc)
    clock = FixedClock(received)
    event = normalize_kite_quote("NSE:INFY", quote(), clock=clock,
                                 availability_delay=timedelta(milliseconds=25))
    assert event.event_time.isoformat() == "2026-11-13T04:45:00+00:00"
    assert event.received_at == received
    assert event.available_at == received.replace(microsecond=25000)
