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
    """Normalize read-only Kite full quotes into the canonical forward-event contract.

    A complete two-sided quote retains displayed quantities, all available depth, volume,
    session OHLC and the raw-payload digest. LTP-only observations remain informational
    ticks and cannot be used as executable paper fills. Missing or malformed observations
    fail the batch unless the caller explicitly requests a rejection ledger.
    """
    import hashlib
    import json

    from ..forward_edge.market_data import KiteQuoteNormalizationError, normalize_kite_quote
    from ..forward_edge.market_events import MarketEvent
    from ..providers import kite_data as kd

    if not symbols or any(not isinstance(symbol, str) or not symbol.strip() for symbol in symbols):
        raise ValueError("symbols must be non-empty strings")
    keys = []
    for value in symbols:
        value = value.strip()
        if ":" in value:
            exchange, symbol = value.split(":", 1)
            exchange = exchange.strip().upper()
            if exchange not in {"NSE", "BSE"} or not symbol.strip() or ":" in symbol:
                raise ValueError("qualified symbols must be NSE:SYMBOL or BSE:SYMBOL")
            keys.append(f"{exchange}:{symbol.strip()}")
        else:
            keys.append(f"NSE:{value}")
    if len(set(keys)) != len(keys):
        raise ValueError("exchange-qualified symbols must be unique")
    raw = (quote_fn or kd.quote)(keys, "full")
    receipt = received_at or datetime.now(timezone.utc)
    if receipt.tzinfo is None or receipt.utcoffset() is None:
        raise ValueError("received_at must be timezone-aware")
    receipt = receipt.astimezone(timezone.utc)
    events: list[MarketEvent] = []

    def reject(key: str, quote: object, reason: str) -> None:
        if rejections_out is None:
            raise KiteQuoteNormalizationError(f"{reason} for {key}")
        raw_hash = hashlib.sha256(json.dumps(quote, sort_keys=True, separators=(",", ":"),
                                           default=str).encode("utf-8")).hexdigest()
        rejections_out.append({"event_sha256": raw_hash, "instrument": key, "reason": reason})

    for key in keys:
        quote = raw.get(key) if isinstance(raw, dict) else None
        if not isinstance(quote, dict):
            reject(key, {"missing_quote": True}, "MISSING_KITE_QUOTE")
            continue
        try:
            event = normalize_kite_quote(key, quote, received_at=receipt)
        except (KiteQuoteNormalizationError, TypeError, ValueError) as exc:
            reject(key, quote, str(exc))
            continue
        payload = dict(event.payload)
        status = quote.get("exchange_status", quote.get("market_status"))
        if status is not None:
            payload["exchange_status"] = str(status)
        events.append(MarketEvent(
            event_id=event.event_id, kind=event.kind, venue=event.venue,
            instrument_id=event.instrument_id, event_time=event.event_time,
            received_at=event.received_at, available_at=event.available_at,
            payload=payload, source=event.source, sequence=event.sequence,
            decision_time=event.decision_time, evidence_id=event.evidence_id,
            source_at=event.source_at, exchange_at=event.exchange_at,
            published_at=event.published_at,
        ))
    return events


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


# ---- merged from main (2026-11-18): definitions main's modules depend on; the branch's own
# ---- definitions above are unchanged.


def _valid_level(level: dict) -> bool:
    try:
        return float(level["price"]) > 0 and int(level["quantity"]) > 0
    except (KeyError, TypeError, ValueError):
        return False

