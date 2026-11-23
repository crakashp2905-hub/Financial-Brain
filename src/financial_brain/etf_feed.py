"""Read-only Kite full-quote polling for the currently referenced NSE ETF universe."""
from __future__ import annotations

import time
from datetime import date, datetime, time as clock_time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .forward_edge.market_data import archive_kite_quote_batch_to_path
from .storage.db import Database

IST = ZoneInfo("Asia/Kolkata")
OPEN = clock_time(9, 15)
CLOSE = clock_time(15, 30)
DEFAULT_ARCHIVE = Path("data") / "etf-market-events.sqlite"
MAX_BATCH = 450


def eligible_symbols(con, as_of: date) -> list[str]:
    """Latest known NSE ETF symbols as of the requested exchange-local date."""
    rows = con.execute(
        """WITH ranked AS (
               SELECT symbol, isin, status,
                      ROW_NUMBER() OVER (PARTITION BY exchange, isin
                                         ORDER BY observed_on DESC) AS rn
               FROM etf_reference_observations
               WHERE exchange = 'NSE' AND observed_on <= ?
           )
           SELECT DISTINCT symbol FROM ranked WHERE rn = 1 AND status = 'LISTED'
           ORDER BY symbol""",
        [as_of],
    ).fetchall()
    return [row[0] for row in rows]


def collect_once(symbols: list[str], *, quote_fn=None, archive_path: Path = DEFAULT_ARCHIVE,
                 received_at: datetime | None = None) -> dict[str, int]:
    """Poll full quotes in broker-sized batches and append timestamped normalized events."""
    if len(set(symbols)) != len(symbols):
        raise ValueError("ETF symbols must be unique")
    if quote_fn is None:
        from .providers.kite_data import quote as quote_fn
    received_at = received_at or datetime.now(timezone.utc)
    if received_at.tzinfo is None or received_at.utcoffset() is None:
        raise ValueError("received_at must be timezone-aware")
    totals = {"requested": len(symbols), "archived": 0, "rejected": 0, "missing": 0}
    keys = [f"NSE:{symbol}" for symbol in symbols]
    for start in range(0, len(keys), MAX_BATCH):
        quotes = quote_fn(keys[start:start + MAX_BATCH], "full")
        counts = archive_kite_quote_batch_to_path(
            archive_path, quotes, received_at=received_at.astimezone(timezone.utc))
        totals["archived"] += counts["archived"]
        totals["rejected"] += counts["rejected"]
        totals["missing"] += len(set(keys[start:start + MAX_BATCH]) - set(quotes))
    return totals


def run(*, until: clock_time = CLOSE, poll_seconds: int = 60, archive_path: Path = DEFAULT_ARCHIVE,
        now_fn=None, sleep=time.sleep, quote_fn=None, db: Database | None = None) -> dict:
    """Poll the current reference universe during NSE hours; no order API is used."""
    if poll_seconds < 1:
        raise ValueError("poll_seconds must be positive")
    now_fn = now_fn or (lambda: datetime.now(IST))
    db = db or Database()
    db.migrate()
    with db.connect() as con:
        symbols = eligible_symbols(con, now_fn().astimezone(IST).date())
    if not symbols:
        return {"symbols": 0, "polls": 0, "archived": 0, "rejected": 0, "missing": 0,
                "message": "no point-in-time NSE ETF reference rows"}
    polls = archived = rejected = missing = 0
    while True:
        now = now_fn().astimezone(IST)
        if now.time() >= until or now.time() >= CLOSE:
            break
        if now.weekday() >= 5 or now.time() < OPEN:
            sleep(min(poll_seconds, 30))
            continue
        counts = collect_once(symbols, quote_fn=quote_fn, archive_path=archive_path,
                              received_at=now)
        polls += 1
        archived += counts["archived"]
        rejected += counts["rejected"]
        missing += counts["missing"]
        sleep(poll_seconds)
    return {"symbols": len(symbols), "polls": polls, "archived": archived,
            "rejected": rejected, "missing": missing, "archive": str(archive_path)}
