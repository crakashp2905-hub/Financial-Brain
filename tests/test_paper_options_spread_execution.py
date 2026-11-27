from datetime import date, datetime, timedelta, timezone

from financial_brain.paper.options import Quote
from financial_brain.paper.options_spread import Leg
from financial_brain.paper.options_spread_execution import (
    SpreadQuoteEvent,
    simulate_debit_vertical_entry,
)


DECISION = datetime(2026, 11, 27, 5, tzinfo=timezone.utc)
EXPIRY = date(2026, 12, 3)


def _leg(token, strike, kind="CE"):
    quote = Quote(token, f"NIFTY-{strike}-{kind}", EXPIRY, 10,
                  DECISION - timedelta(seconds=1), 5, 6, 100, 100)
    return Leg(quote, "NIFTY", kind, strike)


def _event(token, symbol, at, *, bid=4, ask=6, bid_qty=10, ask_qty=10):
    quote = Quote(token, symbol, EXPIRY, 10, at - timedelta(milliseconds=5),
                  bid, ask, bid_qty, ask_qty)
    return SpreadQuoteEvent(quote, at)


def test_asynchronous_vertical_fills_long_first_and_never_exceeds_covered_short():
    long = _leg(1, 25000)
    short = _leg(2, 25100)
    t1, t2, t3, t4, t5 = (DECISION + timedelta(seconds=n) for n in (1, 2, 3, 4, 5))
    events = (
        _event(2, short.quote.symbol, t1, bid=4, bid_qty=10),  # cannot short before long
        _event(1, long.quote.symbol, t1, ask=6, ask_qty=10),
        _event(2, short.quote.symbol, t2, bid=4, bid_qty=10),
        _event(1, long.quote.symbol, t3, ask=6, ask_qty=10),
        _event(2, short.quote.symbol, t4, bid=4, bid_qty=10),
        _event(2, short.quote.symbol, t5, bid=4, bid_qty=10),
    )
    result = simulate_debit_vertical_entry(
        long_leg=long, short_leg=short, lots=2, decision_at=DECISION,
        events=events, max_entry_delay=timedelta(seconds=10),
        max_source_lag=timedelta(seconds=1),
    )
    assert result["status"] == "FILLED_DEFINED_RISK"
    assert result["long_filled_contracts"] == result["short_filled_contracts"] == 20
    assert result["unsupported_naked_short_contracts"] == 0
    assert result["maximum_loss_bound_inr"] > 0
    assert [row["action"] for row in result["fill_events"]] == [
        "BUY_LONG", "SELL_SHORT", "BUY_LONG", "SELL_SHORT"]


def test_partial_long_only_and_missing_long_quotes_remain_safe_no_fill():
    long = _leg(1, 25000)
    short = _leg(2, 25100)
    t1 = DECISION + timedelta(seconds=1)
    result = simulate_debit_vertical_entry(
        long_leg=long, short_leg=short, lots=2, decision_at=DECISION,
        events=(_event(2, short.quote.symbol, t1, bid_qty=20),
                _event(1, long.quote.symbol, t1 + timedelta(seconds=1), ask_qty=10)),
        max_entry_delay=timedelta(seconds=10), max_source_lag=timedelta(seconds=1),
    )
    assert result["status"] == "PARTIAL_LONG_ONLY"
    assert result["long_filled_contracts"] == 10
    assert result["short_filled_contracts"] == 0
    assert result["unmatched_long_contracts"] == 10
    assert result["unsupported_naked_short_contracts"] == 0
    empty = simulate_debit_vertical_entry(
        long_leg=long, short_leg=short, lots=1, decision_at=DECISION, events=(),
        max_entry_delay=timedelta(seconds=10), max_source_lag=timedelta(seconds=1),
    )
    assert empty["status"] == "NO_FILL"
    assert empty["long_filled_contracts"] == empty["short_filled_contracts"] == 0
