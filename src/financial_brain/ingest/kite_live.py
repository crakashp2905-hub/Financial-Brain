"""Live prices, snapshotted and checked against what the archive says.

One read-only quote call for a list of symbols, stored with the time it was taken, and a comparison
with the latest close already in the archive. Two jobs:

1. give the scanner something fresher than yesterday's bhavcopy while the market is open, and
2. be a data-quality check that does not depend on the data it checks: if Kite's last price and our
   adjusted close disagree by more than a corporate action could explain, one of them is wrong, and the
   difference is reported instead of both being trusted.

A quote outside market hours returns the last close, so a "live" price equal to the close is the
expected result on a weekend and says nothing about the market; ``market_open`` is reported so a reader
does not mistake it for confirmation.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..forward_edge.raw_quote_archive import RawQuoteArchive

IST = timezone(timedelta(hours=5, minutes=30))
OPEN, CLOSE = time(9, 15), time(15, 30)
SCHEMA = """
CREATE TABLE IF NOT EXISTS live_quotes (
    taken_at   TIMESTAMP WITH TIME ZONE NOT NULL,
    symbol     VARCHAR NOT NULL,
    last_price DOUBLE,
    open       DOUBLE,
    high       DOUBLE,
    low        DOUBLE,
    prev_close DOUBLE,
    PRIMARY KEY (taken_at, symbol)
);
"""


def market_open(now: datetime | None = None) -> bool:
    now = (now or datetime.now(IST)).astimezone(IST)
    return now.weekday() < 5 and OPEN <= now.time() <= CLOSE


def snapshot(con, symbols: list[str], *, quote_fn=None, now: datetime | None = None) -> dict:
    from ..providers import kite_data as kd
    quote_fn = quote_fn or kd.quote
    con.execute(SCHEMA)
    now = now or datetime.now(IST)
    q = quote_fn([f"NSE:{s}" for s in symbols], "ohlc")
    rows = []
    for key, v in q.items():
        o = v.get("ohlc") or {}
        rows.append((now, key.split(":", 1)[-1], v.get("last_price"), o.get("open"), o.get("high"),
                     o.get("low"), o.get("close")))
    if rows:
        con.executemany("INSERT OR IGNORE INTO live_quotes VALUES (?,?,?,?,?,?,?)", rows)
    missing = sorted(set(symbols) - {r[1] for r in rows})
    return {"taken_at": now, "stored": len(rows), "missing": missing, "market_open": market_open(now)}


def forward_events(symbols: list[str], *, quote_fn=None, received_at: datetime | None = None,
                   ingested_at: datetime | None = None,
                   rejections_out: list[dict] | None = None, clock_provider=None,
                   raw_archive: RawQuoteArchive | None = None,
                   source_timestamp_tolerance: timedelta | None = None):
    """Capture read-only Kite full quotes as forward-edge events.

    An exchange timestamp is mandatory: receipt time is never substituted for it.
    By default the entire batch fails if a requested quote is absent or its clocks are invalid.
    A caller may supply ``rejections_out`` to preserve bad rows by source hash and reason while
    returning only valid normalized events; invalid rows are never repaired or forward-filled.
    """
    from ..forward_edge.market_data import DataError, NormalizedEvent
    from ..forward_edge.session_clock import ClockError, SessionClockProvider
    from ..providers import kite_data as kd

    if not symbols or len(set(symbols)) != len(symbols):
        raise DataError("symbols must be nonempty and unique")
    if source_timestamp_tolerance is not None and (
            not isinstance(source_timestamp_tolerance, timedelta) or source_timestamp_tolerance < timedelta(0)):
        raise DataError("source timestamp tolerance must be a non-negative timedelta")
    keys = [f"NSE:{symbol}" for symbol in symbols]
    if clock_provider is not None and (received_at is not None or ingested_at is not None):
        raise DataError("use the injected clock provider or explicit timestamps, not both")
    clock = clock_provider or SessionClockProvider()
    if received_at is not None:
        if received_at.tzinfo is None or received_at.utcoffset() is None:
            raise DataError("received_at must be timezone-aware")
        received_at = received_at.astimezone(timezone.utc)
    if ingested_at is not None:
        if ingested_at.tzinfo is None or ingested_at.utcoffset() is None:
            raise DataError("ingested_at must be timezone-aware")
        ingested_at = ingested_at.astimezone(timezone.utc)
    if received_at is not None and ingested_at is not None and ingested_at < received_at:
        raise DataError("ingest clock cannot precede source availability")
    raw = (quote_fn or kd.quote)(keys, "full")
    events = []

    def reject(key: str, raw_quote: object, reason: str) -> None:
        if rejections_out is None:
            raise DataError(f"{reason} for {key}")
        raw_hash = hashlib.sha256(json.dumps(raw_quote, sort_keys=True, separators=(",", ":"),
                                         default=str).encode("utf-8")).hexdigest()
        rejections_out.append({"event_sha256": raw_hash, "instrument": key, "reason": reason})

    for key in keys:
        quote = raw.get(key)
        if not isinstance(quote, dict):
            reject(key, {"missing_quote": True}, "MISSING_KITE_QUOTE")
            continue
        stamp = quote.get("timestamp")
        if isinstance(stamp, str):
            try:
                stamp = datetime.fromisoformat(stamp)
            except ValueError:
                reject(key, quote, "INVALID_EXCHANGE_TIMESTAMP")
                continue
        if not isinstance(stamp, datetime):
            reject(key, quote, "MISSING_EXCHANGE_TIMESTAMP")
            continue
        # Kite's exchange timestamps are India-local when the API omits a zone.
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=IST)
        exchange_at = stamp.astimezone(timezone.utc)
        if received_at is None:
            try:
                receipt = clock.receive_time(exchange_at)
            except ClockError as exc:
                raise DataError(str(exc)) from exc
        else:
            receipt = available = received_at
        source_age = receipt - exchange_at
        if source_age < timedelta(0):
            reject(key, quote, "FUTURE_EXCHANGE_TIMESTAMP")
            continue
        if source_timestamp_tolerance is not None and source_age > source_timestamp_tolerance:
            reject(key, quote, "STALE_EXCHANGE_TIMESTAMP")
            continue
        payload = [("exchange", "NSE"), ("source", "kite_connect")]
        raw_hash = hashlib.sha256(json.dumps(quote, sort_keys=True, separators=(",", ":"),
                                          ensure_ascii=False, default=str, allow_nan=False)
                             .encode("utf-8")).hexdigest()
        payload.append(("raw_sha256", raw_hash))
        payload.append(("exchange_timestamp_age_ms", str(int(source_age.total_seconds() * 1000))))
        if source_timestamp_tolerance is not None:
            payload.append(("exchange_timestamp_tolerance_ms",
                            str(int(source_timestamp_tolerance.total_seconds() * 1000))))
        if quote.get("last_price") is not None:
            payload.append(("last_price", str(quote["last_price"])))
        depth = quote.get("depth")
        buys = depth.get("buy", []) if isinstance(depth, dict) else []
        sells = depth.get("sell", []) if isinstance(depth, dict) else []
        best_bid = buys[0] if buys and isinstance(buys[0], dict) else {}
        best_ask = sells[0] if sells and isinstance(sells[0], dict) else {}
        if not _valid_level(best_bid) or not _valid_level(best_ask):
            quote_fidelity = "LTP_ONLY"
        else:
            bid_price, ask_price = Decimal(str(best_bid["price"])), Decimal(str(best_ask["price"]))
            quote_fidelity = ("CROSSED_BOOK" if bid_price > ask_price else
                              "LOCKED_BOOK" if bid_price == ask_price else "EXECUTABLE")
        payload.append(("quote_fidelity", quote_fidelity))
        for side, level in (("bid", best_bid), ("ask", best_ask)):
            if _valid_level(level):
                payload.extend(((f"{side}_price", str(level["price"])),
                                (f"{side}_qty", str(level["quantity"]))))
        for key_name, source in (("volume", "volume"), ("session_high", "ohlc.high"),
                                 ("session_low", "ohlc.low"), ("exchange_status", "mode")):
            ohlc = quote.get("ohlc")
            value = quote.get(source) if "." not in source else (
                ohlc.get(source.split(".")[1]) if isinstance(ohlc, dict) else None)
            if value is not None:
                payload.append((key_name, str(value)))
        for key_name, source in (("circuit_lower", "lower_circuit_limit"),
                                 ("circuit_upper", "upper_circuit_limit"),
                                 ("auction_active", "auction_active"),
                                 ("market_phase", "market_phase")):
            value = quote.get(source)
            if value is not None:
                payload.append((key_name, str(value)))
        try:
            if received_at is None:
                available = clock.availability_time(exchange_at, receipt)
            try:
                ingested = ingested_at or clock.now_utc("ingest")
            except ClockError as exc:
                raise DataError(str(exc)) from exc
            if ingested < available:
                raise DataError("ingest clock cannot precede source availability")
            if raw_archive is not None:
                archive_ref = raw_archive.append_quote(key, quote, received_at=receipt,
                                                      available_at=available)
                if archive_ref.get("payload_sha256") != raw_hash:
                    raise DataError("raw quote archive digest does not match normalized source digest")
                payload.extend((("raw_archive_seq", str(archive_ref["archive_seq"])),
                                ("raw_archive_sha256", str(archive_ref["archive_chain_sha256"]))))
            events.append(NormalizedEvent(
                kind="QUOTE", instrument=key, event_utc=exchange_at,
                received_utc=receipt, available_utc=available, payload=tuple(sorted(payload)),
                ingested_utc=ingested,
            ))
        except DataError:
            reject(key, quote, "INVALID_EVENT_CLOCK_ORDER")
    return events


def historical_bar_events(symbol: str, instrument_token: int, start, as_of, *, fetched_at: datetime,
                          interval: str = "minute", continuous: bool = False, oi: bool = False,
                          candles_fn=None, raw_archive: RawQuoteArchive | None = None):
    """Fetch and optionally archive Kite historical candles as availability-safe BAR events.

    Kite candle timestamps identify the source bar, but a historical download is not
    evidence that the bar was known at that past time. ``fetched_at`` is therefore
    both receipt and availability time. Such events are usable for reproducible
    historical audits, not retroactive prospective-decision claims.
    """
    from decimal import Decimal, InvalidOperation

    from ..forward_edge.market_data import DataError, NormalizedEvent
    from ..providers import kite_data as kd

    if not isinstance(symbol, str) or not symbol.strip():
        raise DataError("symbol is required for historical candle capture")
    if type(instrument_token) is not int or instrument_token <= 0:
        raise DataError("a positive Kite instrument token is required")
    if fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
        raise DataError("candle retrieval time must be timezone-aware")
    available = fetched_at.astimezone(timezone.utc)
    fetch = candles_fn or kd.candles
    rows = fetch(instrument_token, start, as_of, interval=interval,
                 continuous=continuous, oi=oi)
    events = []
    for row in rows:
        if not isinstance(row, dict):
            raise DataError("Kite candle rows must be objects")
        stamp = row.get("ts")
        if isinstance(stamp, str):
            try:
                stamp = datetime.fromisoformat(stamp)
            except ValueError as exc:
                raise DataError("Kite candle timestamp is invalid") from exc
        if not isinstance(stamp, datetime):
            raise DataError("Kite candle timestamp is missing")
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=IST)
        event_at = stamp.astimezone(timezone.utc)
        try:
            prices = {key: Decimal(str(row[key])) for key in ("open", "high", "low", "close")}
            volume = int(row["volume"])
        except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
            raise DataError("Kite candle OHLCV is incomplete or invalid") from exc
        if (any(not value.is_finite() or value <= 0 for value in prices.values())
                or prices["high"] < max(prices["open"], prices["close"], prices["low"])
                or prices["low"] > min(prices["open"], prices["close"]) or volume < 0):
            raise DataError("Kite candle OHLCV values violate price or volume bounds")
        payload = {"source": "kite_historical", "interval": interval,
                   **{key: str(value) for key, value in prices.items()}, "volume": str(volume)}
        if row.get("oi") is not None:
            payload["oi"] = str(row["oi"])
        event = NormalizedEvent(kind="BAR", instrument=symbol, event_utc=event_at,
                                received_utc=available, available_utc=available,
                                payload=tuple(sorted(payload.items())))
        events.append(event)
    events.sort(key=lambda event: (event.event_utc, event.content_sha256()))
    if raw_archive is not None:
        for event in events:
            raw_archive.append_market_event(event, source="kite_historical")
    return events


def _valid_level(level: dict) -> bool:
    try:
        price, quantity = Decimal(str(level["price"])), int(level["quantity"])
        return price.is_finite() and price > 0 and quantity > 0
    except (KeyError, TypeError, ValueError, InvalidOperation):
        return False


def divergence(con, *, tolerance: float = 0.03) -> list[dict]:
    """Latest snapshot against the archive's latest adjusted close, largest gaps first."""
    rows = con.execute("""
        WITH l AS (SELECT symbol, last_price, taken_at FROM live_quotes
                   WHERE taken_at = (SELECT MAX(taken_at) FROM live_quotes)),
             a AS (SELECT ticker, close_adj, business_date,
                          ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY business_date DESC) rn
                   FROM adjusted_prices WHERE close_adj > 0)
        SELECT l.symbol, l.last_price, a.close_adj, a.business_date,
               l.last_price / a.close_adj - 1 AS gap
        FROM l JOIN a ON a.ticker = l.symbol AND a.rn = 1
        WHERE l.last_price IS NOT NULL ORDER BY ABS(l.last_price / a.close_adj - 1) DESC""").fetchall()
    return [{"symbol": r[0], "live": r[1], "archive_close": r[2], "archive_date": r[3], "gap": r[4],
             "flag": abs(r[4]) > tolerance} for r in rows]
