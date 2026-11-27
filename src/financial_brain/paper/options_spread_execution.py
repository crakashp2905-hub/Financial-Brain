"""Sequential quote-driven entry for defined-risk debit spreads."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from ..costs.india import CostModel, Segment, Side
from .options import OptionPaperError, Quote
from .options_spread import Leg


@dataclass(frozen=True)
class SpreadQuoteEvent:
    quote: Quote
    available_at: datetime


def simulate_debit_vertical_entry(*, long_leg: Leg, short_leg: Leg, lots: int,
                                  decision_at: datetime, events: tuple[SpreadQuoteEvent, ...],
                                  max_entry_delay: timedelta,
                                  max_source_lag: timedelta,
                                  costs: CostModel | None = None) -> dict[str, object]:
    """Replay long-first asynchronous fills and cap short quantity at long fills."""
    long_quote, short_quote = long_leg.quote, short_leg.quote
    long_quote.validate()
    short_quote.validate()
    if (type(lots) is not int or lots < 1 or decision_at.tzinfo is None
            or decision_at.utcoffset() is None or max_entry_delay <= timedelta(0)
            or max_source_lag < timedelta(0)):
        raise OptionPaperError("valid lots, decision clock and spread entry windows are required")
    if (long_leg.underlying, long_leg.option_type, long_quote.expiry, long_quote.lot_size) != (
            short_leg.underlying, short_leg.option_type, short_quote.expiry, short_quote.lot_size):
        raise OptionPaperError("spread legs need same underlying, right, expiry and lot size")
    if (long_leg.option_type not in {"CE", "PE"} or long_quote.token == short_quote.token
            or long_quote.symbol == short_quote.symbol):
        raise OptionPaperError("spread requires distinct exact option contracts")
    width = (short_leg.strike - long_leg.strike if long_leg.option_type == "CE"
             else long_leg.strike - short_leg.strike)
    if width <= 0:
        raise OptionPaperError("debit spread strikes are not ordered")
    if len({(event.quote.token, event.available_at) for event in events}) != len(events):
        raise OptionPaperError("spread quote events must be unique by token and availability time")
    if any(event.available_at.tzinfo is None or event.available_at.utcoffset() is None
           for event in events):
        raise OptionPaperError("spread quote availability must be timezone-aware")
    expected_quantity = lots * long_quote.lot_size
    long_filled = 0
    short_filled = 0
    long_cash = 0.0
    short_cash = 0.0
    charges = 0.0
    fill_events: list[dict[str, object]] = []
    quality_rejections: list[str] = []
    cost_model = costs or CostModel()
    cutoff = decision_at + max_entry_delay
    ordered = sorted(events, key=lambda event: (event.available_at,
                                               0 if event.quote.token == long_quote.token else 1))
    for event in ordered:
        quote = event.quote
        available = event.available_at
        if available.tzinfo is None or available.utcoffset() is None:
            raise OptionPaperError("spread quote availability must be timezone-aware")
        if available <= decision_at or available > cutoff:
            continue
        if quote.observed_at.tzinfo is None or quote.observed_at.utcoffset() is None:
            quality_rejections.append("SOURCE_CLOCK_MISSING")
            continue
        if available < quote.observed_at:
            raise OptionPaperError("spread quote cannot be available before observation")
        if available - quote.observed_at > max_source_lag:
            quality_rejections.append("STALE_SOURCE_QUOTE")
            continue
        if quote.token == long_quote.token:
            template, action = long_quote, "BUY_LONG"
            remaining = expected_quantity - long_filled
            fill_quantity = min(remaining, quote.ask_qty) // quote.lot_size * quote.lot_size
            price = quote.ask
        elif quote.token == short_quote.token:
            template, action = short_quote, "SELL_SHORT"
            paired_capacity = long_filled - short_filled
            fill_quantity = min(paired_capacity, quote.bid_qty) // quote.lot_size * quote.lot_size
            price = quote.bid
        else:
            quality_rejections.append("UNKNOWN_CONTRACT_TOKEN")
            continue
        if (quote.symbol, quote.expiry, quote.lot_size) != (
                template.symbol, template.expiry, template.lot_size):
            quality_rejections.append("CONTRACT_MISMATCH")
            continue
        try:
            quote.validate()
        except OptionPaperError:
            quality_rejections.append("INVALID_OR_CROSSED_BOOK")
            continue
        if fill_quantity <= 0:
            continue
        notional = fill_quantity * price
        side = Side.BUY if action == "BUY_LONG" else Side.SELL
        fee = float(cost_model.one_way(turnover=notional, side=side,
                                       segment=Segment.OPT, include_impact=False)["total"])
        if action == "SELL_SHORT" and long_cash - (short_cash + notional) + charges + fee <= 0:
            quality_rejections.append("SHORT_LEG_WOULD_REMOVE_DEBIT_STRUCTURE")
            continue
        charges += fee
        if action == "BUY_LONG":
            long_filled += fill_quantity
            long_cash += notional
        else:
            short_filled += fill_quantity
            short_cash += notional
        fill_events.append({"available_at": available.isoformat(),
                            "observed_at": quote.observed_at.isoformat(),
                            "contract_token": quote.token, "action": action,
                            "quantity": fill_quantity, "premium": price,
                            "fees_inr": fee})
        if long_filled == expected_quantity and short_filled == expected_quantity:
            break
    unmatched_long = long_filled - short_filled
    net_debit = long_cash - short_cash + charges
    if short_filled > long_filled:
        raise AssertionError("internal safety invariant violated: spread became net short")
    if long_filled == 0:
        status = "NO_FILL"
    elif short_filled == expected_quantity and long_filled == expected_quantity:
        status = "FILLED_DEFINED_RISK"
    else:
        status = "PARTIAL_LONG_ONLY" if short_filled == 0 else "PARTIAL_DEFINED_RISK"
    return {"status": status, "requested_contracts_per_leg": expected_quantity,
            "long_filled_contracts": long_filled, "short_filled_contracts": short_filled,
            "unmatched_long_contracts": unmatched_long,
            "unsupported_naked_short_contracts": max(0, short_filled - long_filled),
            "long_premium_paid_inr": long_cash, "short_premium_received_inr": short_cash,
            "fees_inr": charges, "net_debit_and_fees_inr": net_debit,
            "maximum_loss_bound_inr": net_debit if long_filled else 0.0,
            "fill_events": fill_events,
            "rejection_codes": sorted(set(quality_rejections)),
            "expiry_settlement_modelled": False, "broker_order_created": False}
