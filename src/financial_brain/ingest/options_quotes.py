"""Append-only ATM option quote observations from the read-only market watcher.

Separate from the stock quote archive because its paper runner already owns that
DuckDB writer. An incomplete or crossed book is retained with a quality flag, not
silently discarded: missing liquidity is part of a strategy's feasibility record.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb

PATH = Path("data/options_quotes.duckdb")
IST = timezone(timedelta(hours=5, minutes=30))
SCHEMA = """
CREATE TABLE IF NOT EXISTS option_quotes (
    received_at TIMESTAMPTZ NOT NULL,
    source_at TIMESTAMPTZ,
    instrument_token BIGINT NOT NULL,
    tradingsymbol VARCHAR NOT NULL,
    underlying VARCHAR NOT NULL,
    underlying_ltp DOUBLE,
    expiry DATE NOT NULL,
    strike DOUBLE NOT NULL,
    option_type VARCHAR NOT NULL,
    lot_size INTEGER NOT NULL,
    ltp DOUBLE,
    bid DOUBLE,
    ask DOUBLE,
    bid_qty BIGINT,
    ask_qty BIGINT,
    buy_depth JSON,
    sell_depth JSON,
    book_quality VARCHAR NOT NULL,
    PRIMARY KEY (received_at, instrument_token)
);
"""


def _timestamp(value) -> datetime | None:
    if value is None:
        return None
    try:
        d = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return d.replace(tzinfo=IST) if d.tzinfo is None else d.astimezone(IST)


def records(instruments: list[dict], quotes: dict[str, dict], spots: dict[str, dict],
            received_at: datetime) -> list[tuple]:
    """One row per actually returned contract quote, whether valid or not."""
    if received_at.tzinfo is None:
        raise ValueError("received_at must carry a timezone")
    rows = []
    for contract in instruments:
        symbol = contract.get("tradingsymbol")
        q = quotes.get(f"NFO:{symbol}")
        if not q:
            continue
        dep = q.get("depth") or {}
        buy, sell = dep.get("buy") or [], dep.get("sell") or []
        best_buy, best_sell = (buy[0] if buy else {}), (sell[0] if sell else {})
        bid = float(best_buy.get("price") or 0)
        ask = float(best_sell.get("price") or 0)
        quality = "TWO_SIDED" if bid > 0 and ask >= bid else "INCOMPLETE_OR_CROSSED"
        underlying = str(contract["name"])
        spot_quote = spots.get(f"NSE:{underlying}") or {}
        rows.append((received_at, _timestamp(q.get("timestamp")), int(contract["instrument_token"]),
                     symbol, underlying, spot_quote.get("last_price"), str(contract["expiry"]),
                     float(contract["strike"]), contract["instrument_type"], int(contract["lot_size"]),
                     q.get("last_price"), bid or None, ask or None,
                     best_buy.get("quantity"), best_sell.get("quantity"),
                     json.dumps(buy), json.dumps(sell), quality))
    return rows


def store(instruments: list[dict], quotes: dict[str, dict], spots: dict[str, dict],
          received_at: datetime, *, path: Path = PATH) -> int:
    """Append observations to an isolated local database; no broker order API is used."""
    rows = records(instruments, quotes, spots, received_at)
    if not rows:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    try:
        con.execute(SCHEMA)
        con.executemany("INSERT OR IGNORE INTO option_quotes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    finally:
        con.close()
    return len(rows)
