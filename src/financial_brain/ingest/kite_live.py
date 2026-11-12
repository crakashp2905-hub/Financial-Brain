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
                   rejections_out: list[dict] | None = None):
    """Capture read-only Kite full quotes as forward-edge events.

    An exchange timestamp is mandatory: receipt time is never substituted for it.
    By default the entire batch fails if a requested quote is absent or its clocks are invalid.
    A caller may supply ``rejections_out`` to preserve bad rows by source hash and reason while
    returning only valid normalized events; invalid rows are never repaired or forward-filled.
    """
    from ..forward_edge.market_data import DataError, NormalizedEvent
    from ..providers import kite_data as kd

    if not symbols or len(set(symbols)) != len(symbols):
        raise DataError("symbols must be nonempty and unique")
    keys = [f"NSE:{symbol}" for symbol in symbols]
    raw = (quote_fn or kd.quote)(keys, "full")
    receipt = received_at or datetime.now(timezone.utc)
    if receipt.tzinfo is None or receipt.utcoffset() is None:
        raise DataError("received_at must be timezone-aware")
    receipt = receipt.astimezone(timezone.utc)
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
        payload = [("exchange", "NSE"), ("source", "kite_connect")]
        raw_hash = hashlib.sha256(json.dumps(quote, sort_keys=True, separators=(",", ":"),
                                          default=str).encode("utf-8")).hexdigest()
        payload.append(("raw_sha256", raw_hash))
        if quote.get("last_price") is not None:
            payload.append(("last_price", str(quote["last_price"])))
        depth = quote.get("depth")
        buys = depth.get("buy", []) if isinstance(depth, dict) else []
        sells = depth.get("sell", []) if isinstance(depth, dict) else []
        best_bid = buys[0] if buys and isinstance(buys[0], dict) else {}
        best_ask = sells[0] if sells and isinstance(sells[0], dict) else {}
        payload.append(("quote_fidelity", "EXECUTABLE" if _valid_level(best_bid) and _valid_level(best_ask)
                        else "LTP_ONLY"))
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
        try:
            events.append(NormalizedEvent(
                kind="QUOTE", instrument=key, event_utc=stamp.astimezone(timezone.utc),
                received_utc=receipt, available_utc=receipt, payload=tuple(sorted(payload)),
            ))
        except DataError:
            reject(key, quote, "INVALID_EVENT_CLOCK_ORDER")
    return events


def _valid_level(level: dict) -> bool:
    try:
        return float(level["price"]) > 0 and int(level["quantity"]) > 0
    except (KeyError, TypeError, ValueError):
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
