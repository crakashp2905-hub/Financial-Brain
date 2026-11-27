"""Paper-only event-to-trade timing and quote-depth execution simulator."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from math import isfinite
from typing import Literal


@dataclass(frozen=True, slots=True)
class ExchangeSession:
    opens_at: datetime
    closes_at: datetime


@dataclass(frozen=True, slots=True)
class EventQuote:
    exchange_at: datetime
    available_at: datetime
    bid: float
    ask: float
    bid_quantity: int
    ask_quantity: int


@dataclass(frozen=True, slots=True)
class EventOrder:
    published_at: datetime
    detected_at: datetime
    broker_arrival_at: datetime
    side: Literal["BUY", "SELL"]
    quantity: int
    max_wait: timedelta


@dataclass(frozen=True, slots=True)
class SimulatedEventExecution:
    status: Literal["FILLED", "PARTIAL", "NO_FILL"]
    requested_quantity: int
    filled_quantity: int
    average_price: float | None
    gross_value: float
    estimated_cost: float
    unfilled_quantity: int
    detection_delay_ms: float
    broker_delay_ms: float
    after_hours_arrival: bool
    fill_times: tuple[datetime, ...]


def simulate_event_order(
    order: EventOrder,
    sessions: tuple[ExchangeSession, ...],
    quotes: tuple[EventQuote, ...],
    *,
    estimated_cost_per_unit: float,
    max_quote_age: timedelta = timedelta(seconds=2),
) -> SimulatedEventExecution:
    """Fill only on quotes observed after broker arrival and during an open session.

    BUY consumes ask depth; SELL consumes bid depth. Each quote is consumed at most
    once per order. Quotes available after their session close cannot backfill a fill.
    This is a marketable-order simulation, not a broker order or market-depth claim.
    """
    times = (order.published_at, order.detected_at, order.broker_arrival_at)
    if any(value.tzinfo is None or value.utcoffset() is None for value in times):
        raise ValueError("event/order timestamps must be timezone-aware")
    if not order.published_at <= order.detected_at <= order.broker_arrival_at:
        raise ValueError("publication, detection and broker-arrival times are out of order")
    if order.side not in ("BUY", "SELL") or order.quantity <= 0:
        raise ValueError("side and positive order quantity are required")
    if (order.max_wait <= timedelta(0) or max_quote_age <= timedelta(0)
            or not isfinite(estimated_cost_per_unit) or estimated_cost_per_unit < 0):
        raise ValueError("max_wait must be positive and estimated cost nonnegative")
    for session in sessions:
        if (session.opens_at.tzinfo is None or session.closes_at.tzinfo is None
                or session.opens_at >= session.closes_at):
            raise ValueError("exchange sessions require ordered timezone-aware boundaries")
    if any(a.opens_at >= b.opens_at for a, b in zip(sessions, sessions[1:])):
        raise ValueError("exchange sessions must be ordered")
    for quote in quotes:
        if (quote.exchange_at.tzinfo is None or quote.available_at.tzinfo is None
                or not isfinite(quote.bid) or not isfinite(quote.ask)
                or quote.bid <= 0 or quote.ask < quote.bid
                or quote.bid_quantity < 0 or quote.ask_quantity < 0
                or quote.available_at < quote.exchange_at):
            raise ValueError("quote timestamps, prices, spread or depth are invalid")
    if any(a.available_at > b.available_at for a, b in zip(quotes, quotes[1:])):
        raise ValueError("quotes must be ordered by availability time")

    open_arrival = any(session.opens_at <= order.broker_arrival_at <= session.closes_at
                       for session in sessions)
    deadline = order.broker_arrival_at + order.max_wait
    remaining, value, fill_times = order.quantity, 0.0, []
    for quote in quotes:
        if remaining <= 0:
            break
        if quote.exchange_at < order.broker_arrival_at or quote.available_at < order.broker_arrival_at:
            continue
        if quote.available_at > deadline:
            break
        if quote.available_at - quote.exchange_at > max_quote_age:
            continue
        in_session = any(session.opens_at <= quote.exchange_at <= session.closes_at
                         and quote.available_at <= session.closes_at for session in sessions)
        if not in_session:
            continue
        depth = quote.ask_quantity if order.side == "BUY" else quote.bid_quantity
        quantity = min(remaining, depth)
        if quantity == 0:
            continue
        price = quote.ask if order.side == "BUY" else quote.bid
        value += price * quantity
        remaining -= quantity
        fill_times.append(quote.available_at)
        # One marketable order consumes one observed book snapshot; later snapshots
        # may repeat the same resting depth and must not be summed as fresh liquidity.
        break
    filled = order.quantity - remaining
    return SimulatedEventExecution(
        status="NO_FILL" if filled == 0 else "FILLED" if remaining == 0 else "PARTIAL",
        requested_quantity=order.quantity, filled_quantity=filled,
        average_price=None if filled == 0 else value / filled, gross_value=value,
        estimated_cost=filled * estimated_cost_per_unit, unfilled_quantity=remaining,
        detection_delay_ms=(order.detected_at - order.published_at).total_seconds() * 1000,
        broker_delay_ms=(order.broker_arrival_at - order.detected_at).total_seconds() * 1000,
        after_hours_arrival=not open_arrival, fill_times=tuple(fill_times))
