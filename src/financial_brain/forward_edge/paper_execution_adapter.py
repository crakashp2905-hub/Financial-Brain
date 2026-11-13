"""Connect normalized market quotes to the durable forward-session outbox.

This adapter is deterministic paper simulation only. It cannot submit broker orders.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Callable

from financial_brain.forward_edge.execution import (
    OrderIntent,
    Quote,
    execute_or_no_trade,
)
from financial_brain.forward_edge.market_data import NormalizedEvent


class MarketableQuoteSimulator:
    """Callable runner adapter backed by the conservative quote execution model.

    ``tick_by_instrument`` must contain the effective tick for each tradable symbol.
    A valid two-sided quote and displayed size are mandatory; the fill never uses LTP.
    """

    def __init__(self, tick_by_instrument: dict[str, Decimal], *,
                 latency: timedelta = timedelta(0),
                 feed_health: Callable[[list[NormalizedEvent]], tuple[bool, list[str]]] | None = None) -> None:
        if latency < timedelta(0):
            raise ValueError("latency cannot be negative")
        if not tick_by_instrument or any(tick <= 0 for tick in tick_by_instrument.values()):
            raise ValueError("positive instrument ticks are required")
        self.tick_by_instrument = dict(tick_by_instrument)
        self.latency = latency
        self.feed_health = feed_health or (lambda _events: (True, []))

    def __call__(self, decision: dict, intent_id: str,
                 events: list[NormalizedEvent]) -> dict:
        instrument = str(decision["instrument"])
        tick = self.tick_by_instrument[instrument]
        decision_at = datetime.fromisoformat(str(decision["decision_at_utc"]))
        quantity = decision["quantity"]
        if type(quantity) is not int or quantity <= 0:
            raise ValueError("decision quantity must be a positive integer")
        intent = OrderIntent(
            instrument=instrument,
            side=str(decision["side"]),
            quantity=quantity,
            decision_utc=decision_at,
            tick=tick,
            limit=Decimal(str(decision["limit"])) if decision.get("limit") is not None else None,
        )
        quotes: list[Quote] = []
        quote_events: dict[int, NormalizedEvent] = {}
        for event in events:
            if event.kind != "QUOTE" or event.instrument != instrument:
                continue
            payload = dict(event.payload)
            try:
                quote = Quote(
                    available_utc=event.available_utc,
                    bid=Decimal(payload["bid_price"]),
                    ask=Decimal(payload["ask_price"]),
                    bid_qty=int(payload["bid_qty"]),
                    ask_qty=int(payload["ask_qty"]),
                )
            except (KeyError, TypeError, ValueError, InvalidOperation):
                continue
            if (not quote.bid.is_finite() or not quote.ask.is_finite()
                    or quote.bid <= 0 or quote.ask < quote.bid
                    or quote.bid_qty <= 0 or quote.ask_qty <= 0):
                continue
            quotes.append(quote)
            quote_events[id(quote)] = event

        feed_ok, reasons = self.feed_health(events)
        result = execute_or_no_trade(feed_ok, reasons, intent, quotes, self.latency)
        selected = None
        if result.filled_qty:
            cutoff = decision_at + self.latency
            selected = min((q for q in quotes if q.available_utc >= cutoff),
                           key=lambda q: q.available_utc)
        quote_event = quote_events.get(id(selected)) if selected is not None else None
        receipt = {
            "receipt_id": f"sim-{intent_id}",
            "simulator": "marketable-quote-v1",
            "status": result.status,
            "reason": result.reason,
            "fidelity": result.fidelity,
            "instrument": instrument,
            "side": intent.side,
            "requested_quantity": intent.quantity,
            "queue_position_known": result.queue_position_known,
            "quote_event_sha256s": sorted(event.content_sha256() for event in quote_events.values()),
            "fills": [],
        }
        if result.filled_qty and result.avg_price is not None and selected is not None and quote_event is not None:
            quote_hash = quote_event.content_sha256()
            receipt["simulated_execution_at_utc"] = selected.available_utc.isoformat()
            receipt["fills"] = [{
                "fill_id": hashlib.sha256(f"{intent_id}:{quote_hash}".encode()).hexdigest(),
                "quantity": result.filled_qty,
                "price": str(result.avg_price),
                "quote_event_sha256": quote_hash,
                "quote_available_utc": selected.available_utc.isoformat(),
            }]
        return receipt
