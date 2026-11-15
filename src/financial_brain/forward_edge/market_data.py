"""Normalize the existing Kite read-only quote runner into forward-edge market events.

Kite quote packets expose an exchange `timestamp`, last trade time, and bid/ask depth.
The event clock uses the quote packet timestamp; receipt and availability use the local
observation clock. A missing exchange timestamp or two-sided book is rejected rather than
replaced with the receipt time.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo
from pathlib import Path

from financial_brain.forward_edge.market_events import EventKind, MarketEvent
from financial_brain.forward_edge.quote_archive import QuoteArchive

IST = ZoneInfo("Asia/Kolkata")


class KiteQuoteNormalizationError(ValueError):
    """Kite quote did not contain trustworthy clocks and a two-sided top of book."""


def _exchange_timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip())
        except ValueError as exc:
            raise KiteQuoteNormalizationError("Kite exchange timestamp is malformed") from exc
    else:
        raise KiteQuoteNormalizationError("Kite exchange timestamp is missing")
    # Kite's REST response serializes exchange time without an offset; interpret that
    # documented exchange-local clock as India Standard Time. Never infer from receipt.
    return parsed.replace(tzinfo=IST) if parsed.tzinfo is None else parsed


def normalize_kite_quote(instrument: str, quote: dict[str, Any], *, received_at: datetime) -> MarketEvent:
    """Convert one full Kite quote to a source-labeled event with explicit clocks."""
    if received_at.tzinfo is None or received_at.utcoffset() is None:
        raise KiteQuoteNormalizationError("received_at must be timezone aware")
    try:
        venue, symbol = instrument.split(":", 1)
    except ValueError as exc:
        raise KiteQuoteNormalizationError("instrument must be EXCHANGE:TRADINGSYMBOL") from exc
    if not venue.strip() or not symbol.strip():
        raise KiteQuoteNormalizationError("instrument venue and symbol are required")
    timestamp = _exchange_timestamp(quote.get("timestamp"))
    try:
        last_price = float(quote["last_price"])
        volume = int(quote.get("volume", 0))
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise KiteQuoteNormalizationError("Kite quote contains invalid last price or volume") from exc
    if not math.isfinite(last_price) or last_price <= 0 or volume < 0:
        raise KiteQuoteNormalizationError("Kite quote last price must be positive and volume non-negative")
    depth = quote.get("depth")
    bids = depth.get("buy") if isinstance(depth, dict) else None
    asks = depth.get("sell") if isinstance(depth, dict) else None
    bid_rows = bids if isinstance(bids, list) else []
    ask_rows = asks if isinstance(asks, list) else []
    executable = bool(bid_rows) and bool(ask_rows)
    kind = EventKind.QUOTE if executable else EventKind.TICK
    if executable:
        try:
            bid, ask = float(bid_rows[0]["price"]), float(ask_rows[0]["price"])
            payload: dict[str, Any] = {
                "bid": bid, "ask": ask,
                "bid_quantity": int(bid_rows[0].get("quantity", 0)),
                "ask_quantity": int(ask_rows[0].get("quantity", 0)),
                "last_price": last_price,
                "volume": volume,
                "depth": {"buy": bid_rows, "sell": ask_rows},
            }
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise KiteQuoteNormalizationError("Kite quote contains invalid depth") from exc
    else:
        payload = {"price": last_price, "volume": volume,
                   "non_executable_reason": "TWO_SIDED_DEPTH_UNAVAILABLE"}
        if quote.get("last_quantity") is not None:
            try:
                payload["quantity"] = int(quote["last_quantity"])
            except (TypeError, ValueError, OverflowError) as exc:
                raise KiteQuoteNormalizationError("Kite last-trade quantity is invalid") from exc
    ohlc = quote.get("ohlc") or {}
    if isinstance(ohlc, dict):
        for source_key, payload_key in (("open", "session_open"), ("high", "session_high"),
                                        ("low", "session_low")):
            if ohlc.get(source_key) is not None:
                payload[payload_key] = float(ohlc[source_key])
    for key in ("lower_circuit_limit", "upper_circuit_limit"):
        if quote.get(key) is not None:
            payload[key] = float(quote[key])
    packet_hash = hashlib.sha256(json.dumps(quote, sort_keys=True, default=str,
                                             separators=(",", ":")).encode("utf-8")).hexdigest()
    receipt_iso = received_at.isoformat()
    event_id = f"kite:{venue.upper()}:{symbol}:{timestamp.isoformat()}:{receipt_iso}:{packet_hash[:16]}"
    return MarketEvent(event_id=event_id, kind=kind, venue=venue.upper(),
                       instrument_id=f"{venue.upper()}:{symbol}", event_time=timestamp,
                       received_at=received_at, available_at=received_at, payload=payload,
                       source="KITE_CONNECT_READ_ONLY", evidence_id=packet_hash)


def archive_kite_quote_batch(archive: QuoteArchive, quotes: dict[str, dict[str, Any]], *,
                             received_at: datetime) -> dict[str, int]:
    """Append normalized observations from one existing Kite quote response batch."""
    counts = {"received": len(quotes), "archived": 0, "rejected": 0}
    for instrument, quote in sorted(quotes.items()):
        try:
            event = normalize_kite_quote(instrument, quote, received_at=received_at)
            archive.append(event)
            counts["archived"] += 1
        except (KiteQuoteNormalizationError, ValueError, TypeError):
            counts["rejected"] += 1
    return counts


def archive_kite_quote_batch_to_path(path: Path, quotes: dict[str, dict[str, Any]], *,
                                    received_at: datetime) -> dict[str, int]:
    """Persist one poll batch durably without retaining a file handle across runner exits."""
    archive = QuoteArchive(path)
    try:
        return archive_kite_quote_batch(archive, quotes, received_at=received_at)
    finally:
        archive.close()
