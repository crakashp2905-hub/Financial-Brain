"""Connect normalized market quotes to the durable forward-session outbox.

This adapter is deterministic paper simulation only. It cannot submit broker orders.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Callable

from financial_brain.forward_edge.execution import (
    OrderIntent,
    Quote,
    execute_or_no_trade,
    quote_arrival_shortfall,
)
from financial_brain.forward_edge.market_data import NormalizedEvent


class MarketableQuoteSimulator:
    """Callable runner adapter backed by the conservative quote execution model.

    ``tick_by_instrument`` must contain the effective tick for each tradable symbol.
    A valid two-sided quote and displayed size are mandatory; the fill never uses LTP.
    """

    def __init__(self, tick_by_instrument: dict[str, Decimal], *,
                 execution_model_id: str = "quote-v1",
                 fee_schedule_sha256: str,
                 latency: timedelta = timedelta(0),
                 feed_health: Callable[[list[NormalizedEvent]], tuple[bool, list[str]]] | None = None) -> None:
        if latency < timedelta(0):
            raise ValueError("latency cannot be negative")
        if not tick_by_instrument or any(tick <= 0 for tick in tick_by_instrument.values()):
            raise ValueError("positive instrument ticks are required")
        if not execution_model_id.strip():
            raise ValueError("immutable execution_model_id is required")
        if not re.fullmatch(r"[0-9a-f]{64}", fee_schedule_sha256):
            raise ValueError("fee_schedule_sha256 must be a lowercase SHA-256")
        self.tick_by_instrument = dict(tick_by_instrument)
        self.latency = latency
        self.execution_model_id = execution_model_id
        self.fee_schedule_sha256 = fee_schedule_sha256
        self.execution_model_sha256 = _sha256({
            "model_id": execution_model_id,
            "simulator": "marketable-quote-v1",
            "ticks": {key: str(value) for key, value in sorted(tick_by_instrument.items())},
        })
        self.latency_model_sha256 = _sha256({
            "model": "fixed-latency-v1", "latency_microseconds": latency // timedelta(microseconds=1),
        })
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
        data_snapshot_sha256 = _sha256(sorted(event.content_sha256() for event in quote_events.values()))
        quote_inputs = sorted((_quote_input_ref(event) for event in quote_events.values()),
                              key=lambda row: (row["available_utc"], row["quote_id"]))
        arrival_diagnostic = None
        baselines = [q for q in quotes if q.available_utc <= decision_at]
        if selected is not None and quote_event is not None and baselines:
            baseline = max(baselines, key=lambda q: q.available_utc)
            modeled_price = baseline.ask if intent.side == "BUY" else baseline.bid
            arrival_diagnostic = quote_arrival_shortfall(
                side=intent.side, modeled_price=modeled_price, requested_qty=quantity,
                decision_utc=decision_at, latency=self.latency, quote=selected,
                source_sha256=quote_event.content_sha256(),
            )
        receipt = {
            "receipt_id": f"sim-{intent_id}",
            "simulator": "marketable-quote-v1",
            "execution_model_id": self.execution_model_id,
            "execution_model_sha256": self.execution_model_sha256,
            "fee_schedule_sha256": self.fee_schedule_sha256,
            "latency_model_sha256": self.latency_model_sha256,
            "data_snapshot_sha256": data_snapshot_sha256,
            "status": result.status,
            "reason": result.reason,
            "fidelity": result.fidelity,
            "instrument": instrument,
            "side": intent.side,
            "requested_quantity": intent.quantity,
            "queue_position_known": result.queue_position_known,
            "quote_event_sha256s": sorted(event.content_sha256() for event in quote_events.values()),
            "quote_inputs": quote_inputs,
            "used_quote_input_id": _quote_input_ref(quote_event)["quote_id"] if quote_event else None,
            "fills": [],
            "quote_arrival_shortfall": arrival_diagnostic,
        }
        if result.filled_qty and result.avg_price is not None and selected is not None and quote_event is not None:
            quote_hash = quote_event.content_sha256()
            receipt["simulated_execution_at_utc"] = selected.available_utc.isoformat()
            receipt["fills"] = [{
                "fill_id": hashlib.sha256(f"{intent_id}:{quote_hash}".encode()).hexdigest(),
                "quantity": result.filled_qty,
                "price": str(result.avg_price),
                "quote_event_sha256": quote_hash,
                "quote_input_id": _quote_input_ref(quote_event)["quote_id"],
                "quote_available_utc": selected.available_utc.isoformat(),
                "execution_model_id": self.execution_model_id,
                "execution_model_sha256": self.execution_model_sha256,
                "fee_schedule_sha256": self.fee_schedule_sha256,
                "latency_model_sha256": self.latency_model_sha256,
                "data_snapshot_sha256": data_snapshot_sha256,
            }]
        return receipt


def _sha256(value: object) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _quote_input_ref(event: NormalizedEvent) -> dict:
    """Return a stable input ID backed by the raw archive when the event has one."""
    digest = event.content_sha256()
    payload = dict(event.payload)
    sequence = payload.get("raw_archive_seq")
    chain = payload.get("raw_archive_sha256")
    if sequence is not None and chain is not None:
        quote_id = f"raw:{sequence}:{chain}"
    else:
        quote_id = f"event:{digest}"
    return {"quote_id": quote_id, "event_sha256": digest,
            "available_utc": event.available_utc.astimezone(timezone.utc).isoformat(),
            "raw_archive_seq": sequence, "raw_archive_chain_sha256": chain}
