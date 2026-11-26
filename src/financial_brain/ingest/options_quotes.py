"""Append-only ATM option quote observations from the read-only market watcher.

Separate from the stock quote archive because its paper runner already owns that
DuckDB writer. An incomplete or crossed book is retained with a quality flag, not
silently discarded: missing liquidity is part of a strategy's feasibility record.
"""
from __future__ import annotations

import json
import math
import os
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import duckdb
from .kite_fno import RATE, implied_vol

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
    available_at TIMESTAMPTZ,
    spread_percent DOUBLE,
    implied_vol_mid DOUBLE,
    implied_vol_price_basis VARCHAR,
    implied_vol_risk_free_rate DOUBLE,
    minimum_tick DOUBLE,
    session_phase VARCHAR,
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
        bid_qty = int(best_buy.get("quantity") or 0)
        ask_qty = int(best_sell.get("quantity") or 0)
        quality = "TWO_SIDED" if bid > 0 and ask >= bid and bid_qty > 0 and ask_qty > 0 \
            else "INCOMPLETE_OR_CROSSED"
        underlying = str(contract["name"])
        spot_quote = spots.get(f"NSE:{underlying}") or {}
        spot_value = float(spot_quote["last_price"]) if spot_quote.get("last_price") else None
        mid = (bid + ask) / 2 if quality == "TWO_SIDED" else None
        spread_percent = (100 * (ask - bid) / mid) if mid else None
        tick_raw = contract.get("tick_size")
        try:
            tick_candidate = float(str(tick_raw)) if tick_raw not in (None, "") else None
        except (TypeError, ValueError, OverflowError):
            tick_candidate = None
        tick = tick_candidate if tick_candidate is not None and math.isfinite(tick_candidate) \
            and tick_candidate > 0 else None
        session_phase = str(q.get("session_phase") or "UNKNOWN").strip().upper()
        iv_mid = None
        iv_basis = "UNAVAILABLE"
        if mid is not None and spot_value is not None and spot_value > 0:
            try:
                expiry_day = date.fromisoformat(str(contract["expiry"]))
                expiry_close = datetime.combine(expiry_day, time(15, 30), tzinfo=IST)
                year_fraction = max(0.0, (expiry_close - received_at.astimezone(IST)).total_seconds()) \
                    / (365 * 24 * 60 * 60)
                if year_fraction > 0:
                    iv_mid = implied_vol(mid, spot_value, float(contract["strike"]), year_fraction,
                                         str(contract["instrument_type"]), RATE)
                    iv_basis = "OBSERVED_BID_ASK_MID_RESEARCH_ONLY" if iv_mid is not None else "UNSOLVABLE"
            except (TypeError, ValueError, OverflowError):
                iv_basis = "INVALID_CONTRACT_OR_SPOT"
        rows.append((received_at, _timestamp(q.get("timestamp")), int(contract["instrument_token"]),
                     symbol, underlying, spot_value, str(contract["expiry"]),
                     float(contract["strike"]), contract["instrument_type"], int(contract["lot_size"]),
                     q.get("last_price"), bid or None, ask or None,
                     bid_qty or None, ask_qty or None,
                     json.dumps(buy), json.dumps(sell), received_at, spread_percent, iv_mid, iv_basis,
                     RATE,
                     tick, session_phase, quality))
    return rows


def licensed() -> bool:
    """Only the owner can assert an express right to retain broker market data."""
    return os.environ.get("FB_OPTIONS_ARCHIVE_LICENSE_APPROVED") == "1"


def store(instruments: list[dict], quotes: dict[str, dict], spots: dict[str, dict],
          received_at: datetime, *, path: Path = PATH) -> int:
    """Append only with explicit license acknowledgement; no broker order API is used."""
    if not licensed():
        return 0
    rows = records(instruments, quotes, spots, received_at)
    if not rows:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    try:
        con.execute(SCHEMA)
        for column, kind in (("available_at", "TIMESTAMPTZ"), ("spread_percent", "DOUBLE"),
                             ("implied_vol_mid", "DOUBLE"), ("implied_vol_price_basis", "VARCHAR"),
                             ("implied_vol_risk_free_rate", "DOUBLE"), ("minimum_tick", "DOUBLE"),
                             ("session_phase", "VARCHAR")):
            con.execute(f"ALTER TABLE option_quotes ADD COLUMN IF NOT EXISTS {column} {kind}")
        con.executemany("""INSERT OR IGNORE INTO option_quotes
            (received_at, source_at, instrument_token, tradingsymbol, underlying, underlying_ltp, expiry,
             strike, option_type, lot_size, ltp, bid, ask, bid_qty, ask_qty, buy_depth, sell_depth,
             available_at, spread_percent, implied_vol_mid, implied_vol_price_basis,
             implied_vol_risk_free_rate, minimum_tick, session_phase, book_quality)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
    finally:
        con.close()
    return len(rows)
