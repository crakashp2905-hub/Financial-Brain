"""Kite Connect, read only: instruments, intraday candles, quotes.

## Why this exists, and what it unlocks

Four strategies in ``evaluation/timeseries.py`` - dual_thrust, r_breaker, ghost_trader and
dynamic_breakout_ii - are excluded on a data fact rather than an opinion: they key off the
session's opening range and this archive is daily. Kite's historical API serves **minute
bars**, which makes them testable for the first time. That, not realism, is the concrete
reason to connect.

## No order can be placed from this package

There is no order endpoint here, in ``kite.py``, or anywhere else in ``providers``. That
is the first gate in [[Kite readiness]], and it is a *structural* claim rather than a
promise about behaviour: ``ENDPOINTS`` below is the complete set of paths this module will
build, every one a GET, and ``_get`` refuses anything outside it. A reader asking "can this
system place an order" can answer it by reading twenty lines.

The account's own IP allowlist is a second, independent barrier the owner controls. Two
locks, neither relying on the other.

## Rate limits

Kite publishes them and they are not suggestions - exceeding them risks the owner's
account, which is a cost this project has no right to impose. ``MIN_INTERVAL_S`` spaces
calls at the documented ceiling for the historical endpoint (3/second), with a single
shared clock so concurrent callers cannot defeat it.

## Point in time, on live data

A backtest reads a table that cannot change. A live loop reads a stream that arrives while
it runs, which makes look-ahead easy and invisible - and this project has already shipped
two look-aheads in *static* data, one of them hidden in a filter rather than a signal.
Every candle fetch therefore carries an explicit ``to`` bound, and ``as_of`` is required
rather than defaulted to "now", so a caller must state the moment it is reasoning from.
"""
from __future__ import annotations

import csv
import io
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime

API = "https://api.kite.trade"
MIN_INTERVAL_S = 1 / 3          # Kite's documented ceiling for historical data
MAX_QUOTE_SYMBOLS = 500         # Kite's documented cap per quote call

#: Every path this module will build. All GET, none transactional. `_get` refuses any
#: URL that does not start with one of these, so a typo cannot become a POST to /orders.
ENDPOINTS = (
    "/instruments",             # the tradable universe and its instrument tokens
    "/instruments/historical/",  # candles: minute, day, and the intervals between
    "/quote",                   # full quote including depth
    "/quote/ohlc",              # open/high/low/close and last price
    "/quote/ltp",               # last traded price only
)

INTERVALS = ("minute", "3minute", "5minute", "10minute", "15minute", "30minute",
             "60minute", "day")

_clock = threading.Lock()
_last_call = 0.0


class KiteNotConfigured(RuntimeError):
    """Credentials absent. Set them in the environment; nothing here prompts."""


class KiteDataError(RuntimeError):
    """Kite refused a request. The message carries its reason, never a credential."""


def credentials() -> tuple[str, str]:
    """API key from the environment, access token from the environment or today's file."""
    from .kite_login import stored_token
    key = os.environ.get("KITE_API_KEY")
    token = os.environ.get("KITE_ACCESS_TOKEN") or stored_token()
    if not key:
        raise KiteNotConfigured("set KITE_API_KEY in the environment")
    if not token:
        raise KiteNotConfigured(
            "no Kite access token for today - run the login flow "
            "(tokens expire each morning around 6am IST)")
    return key, token


def _throttle() -> None:
    global _last_call
    with _clock:
        wait = MIN_INTERVAL_S - (time.monotonic() - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.monotonic()


def _get(path: str, params: dict | None = None, *, timeout: int = 30) -> dict:
    """The only way this module talks to Kite. GET, allowlisted path, throttled."""
    if not any(path.startswith(p) for p in ENDPOINTS):
        raise KiteDataError(f"refusing {path!r}: not a read endpoint this module serves")
    key, token = credentials()
    url = f"{API}{path}"
    if params:
        # doseq, because the quote endpoints take a repeated `i=` for each symbol.
        url = f"{url}?{urllib.parse.urlencode(params, doseq=True)}"
    req = urllib.request.Request(url, method="GET", headers={
        "X-Kite-Version": "3", "Authorization": f"token {key}:{token}"})
    _throttle()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        raise KiteDataError(f"{e.code} from {path}: {e.read()[:200]!r}") from e
    except OSError as e:
        raise KiteDataError(f"{path}: {e}") from e

    if raw[:1] not in (b"{", b"["):
        return {"status": "success", "raw": raw}       # /instruments returns CSV
    body = json.loads(raw)
    if body.get("status") != "success":
        raise KiteDataError(f"Kite error on {path}: {body.get('message', body)}")
    return body


def instruments(exchange: str = "NSE") -> list[dict]:
    """The tradable universe, with the instrument tokens every other call needs."""
    body = _get(f"/instruments/{exchange}" if exchange else "/instruments")
    raw = body.get("raw", b"")
    return list(csv.DictReader(io.StringIO(raw.decode("utf-8", errors="replace"))))


def token_for(tradingsymbol: str, instrument_list: list[dict] | None = None,
              exchange: str = "NSE") -> int | None:
    """Instrument token for a symbol, resolved at run time rather than hard-coded."""
    rows = instrument_list if instrument_list is not None else instruments(exchange)
    want = tradingsymbol.strip().upper()
    for r in rows:
        if (r.get("tradingsymbol") or "").strip().upper() == want and \
                (r.get("segment") or "").upper() in ("NSE", "INDICES"):
            return int(r["instrument_token"])
    return None


def candles(instrument_token: int, start: date | datetime, as_of: date | datetime, *,
            interval: str = "minute") -> list[dict]:
    """Candles from ``start`` up to ``as_of``, inclusive.

    ``as_of`` is required, not defaulted to now. On a live stream the difference between
    "everything up to the moment I am reasoning about" and "everything available" is
    exactly the look-ahead that static backtests hide, and this project has shipped two of
    those already.
    """
    if interval not in INTERVALS:
        raise KiteDataError(f"interval must be one of {INTERVALS}")
    fmt = "%Y-%m-%d %H:%M:%S" if isinstance(start, datetime) else "%Y-%m-%d"
    body = _get(f"/instruments/historical/{instrument_token}/{interval}",
                {"from": start.strftime(fmt), "to": as_of.strftime(fmt)})
    out = []
    for row in body["data"]["candles"]:
        ts, o, h, lo, c, vol, *_ = (*row, None)[:6] if len(row) < 6 else row
        out.append({"ts": ts, "open": o, "high": h, "low": lo, "close": c,
                    "volume": vol})
    return out


def quote(symbols: list[str], kind: str = "ohlc") -> dict:
    """Live quotes. ``kind`` is 'ltp', 'ohlc' or 'full'.

    Symbols are ``EXCHANGE:TRADINGSYMBOL``, e.g. ``NSE:RELIANCE``.
    """
    paths = {"ltp": "/quote/ltp", "ohlc": "/quote/ohlc", "full": "/quote"}
    if kind not in paths:
        raise KiteDataError(f"kind must be one of {sorted(paths)}")
    if not symbols:
        return {}
    if len(symbols) > MAX_QUOTE_SYMBOLS:
        raise KiteDataError(f"at most {MAX_QUOTE_SYMBOLS} symbols per call, "
                            f"got {len(symbols)}")
    body = _get(paths[kind], {"i": symbols})
    return body.get("data", {})


def quote_url(symbols: list[str], kind: str = "ohlc") -> str:
    """The quote URL, exposed so the repeated ``i=`` parameter is testable."""
    paths = {"ltp": "/quote/ltp", "ohlc": "/quote/ohlc", "full": "/quote"}
    query = "&".join(f"i={urllib.parse.quote(s)}" for s in symbols)
    return f"{API}{paths[kind]}?{query}"
