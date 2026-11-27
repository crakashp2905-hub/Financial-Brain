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
import hashlib
import io
import json
import math
import os
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable

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

class TokenBucket:
    """Thread-safe token bucket with injectable time for deterministic verification."""

    def __init__(self, *, rate_per_second: float, capacity: float = 1.0,
                 clock: Callable[[], float] = time.monotonic,
                 sleeper: Callable[[float], None] = time.sleep) -> None:
        if (not math.isfinite(rate_per_second) or rate_per_second <= 0 or
                not math.isfinite(capacity) or capacity < 1):
            raise ValueError("token-bucket rate must be positive and capacity at least one")
        self.rate_per_second = float(rate_per_second)
        self.capacity = float(capacity)
        self._clock = clock
        self._sleep = sleeper
        self._tokens = self.capacity
        self._updated_at = self._clock()
        self._lock = threading.Lock()

    def acquire(self, tokens: float = 1.0) -> None:
        if not math.isfinite(tokens) or tokens <= 0 or tokens > self.capacity:
            raise ValueError("requested token count must be positive and within bucket capacity")
        with self._lock:
            while True:
                now = self._clock()
                elapsed = max(0.0, now - self._updated_at)
                self._tokens = min(self.capacity, self._tokens + elapsed * self.rate_per_second)
                self._updated_at = now
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return
                self._sleep((tokens - self._tokens) / self.rate_per_second)

    def reset(self) -> None:
        """Restore a full bucket; intended for isolated tests and controlled startup."""
        with self._lock:
            self._tokens = self.capacity
            self._updated_at = self._clock()


@dataclass(frozen=True)
class QuoteSnapshot:
    """Observed quote coverage for an explicit, bounded subscription set."""

    requested_symbols: tuple[str, ...]
    observed_symbols: tuple[str, ...]
    missing_symbols: tuple[str, ...]
    unexpected_symbols: tuple[str, ...]
    batch_count: int
    quotes: dict[str, dict]
    claim_scope: str = "EXPLICIT_REQUESTED_SUBSCRIPTIONS_ONLY"
    market_wide_coverage_claim: bool = False

    @property
    def status(self) -> str:
        return "COMPLETE" if not self.missing_symbols else "INCOMPLETE"

    @property
    def coverage_fraction(self) -> float:
        return len(self.observed_symbols) / len(self.requested_symbols)

    def sha256(self) -> str:
        body = {"requested_symbols": self.requested_symbols,
                "observed_symbols": self.observed_symbols,
                "missing_symbols": self.missing_symbols,
                "unexpected_symbols": self.unexpected_symbols,
                "batch_count": self.batch_count, "quotes": self.quotes,
                "claim_scope": self.claim_scope,
                "market_wide_coverage_claim": self.market_wide_coverage_claim}
        encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


MAX_QUOTE_BATCHES_PER_SNAPSHOT = 20


class KiteNotConfigured(RuntimeError):
    """Credentials absent. Set them in the environment; nothing here prompts."""


class KiteDataError(RuntimeError):
    """Kite refused a request. The message carries its reason, never a credential."""


class KiteAuthError(KiteDataError):
    """Kite rejected the current session; a new owner login may be required."""


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


_rate_limiter = TokenBucket(rate_per_second=1 / MIN_INTERVAL_S, capacity=1.0)


def _throttle() -> None:
    """Acquire one shared historical/quote API request token (no burst above 3/s)."""
    _rate_limiter.acquire()


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
        if e.code in (401, 403):
            raise KiteAuthError(f"Kite session rejected (HTTP {e.code}) on {path}") from e
        raise KiteDataError(f"{e.code} from {path}: {e.read()[:200]!r}") from e
    except OSError as e:
        raise KiteDataError(f"{path}: {e}") from e

    if raw[:1] not in (b"{", b"["):
        return {"status": "success", "raw": raw}       # /instruments returns CSV
    body = json.loads(raw)
    if body.get("status") != "success":
        if body.get("error_type") == "TokenException":
            raise KiteAuthError(f"Kite session rejected on {path}")
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
            interval: str = "minute", continuous: bool = False, oi: bool = False) -> list[dict]:
    """Candles from ``start`` up to ``as_of``, inclusive.

    ``as_of`` is required, not defaulted to now. On a live stream the difference between
    "everything up to the moment I am reasoning about" and "everything available" is
    exactly the look-ahead that static backtests hide, and this project has shipped two of
    those already.
    """
    if interval not in INTERVALS:
        raise KiteDataError(f"interval must be one of {INTERVALS}")
    fmt = "%Y-%m-%d %H:%M:%S" if isinstance(start, datetime) else "%Y-%m-%d"
    params: dict[str, str | int] = {"from": start.strftime(fmt), "to": as_of.strftime(fmt)}
    if continuous:
        params["continuous"] = 1            # futures: expired contracts stitched into one series (day interval)
    if oi:
        params["oi"] = 1
    body = _get(f"/instruments/historical/{instrument_token}/{interval}", params)
    out = []
    for row in body["data"]["candles"]:
        ts, o, h, lo, c, vol = (list(row) + [None] * 6)[:6]
        out.append({"ts": ts, "open": o, "high": h, "low": lo, "close": c, "volume": vol,
                    "oi": row[6] if len(row) > 6 else None})
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


def quote_snapshot(symbols: list[str], kind: str = "full", *,
                   max_batches: int = MAX_QUOTE_BATCHES_PER_SNAPSHOT,
                   max_jitter_s: float = 0.0,
                   jitter_source: Callable[[float, float], float] = random.SystemRandom().uniform,
                   sleeper: Callable[[float], None] = time.sleep) -> QuoteSnapshot:
    """Fetch a bounded subscription set in explicit batches and report missing coverage.

    Backpressure is fail-fast: no requests are started if the frozen subscription set
    exceeds ``max_batches``. Requests remain sequential and use the shared token bucket;
    optional jitter decorrelates multi-batch polling. A complete result describes only
    the requested symbols and never implies market-wide or option-chain completeness.
    """
    if type(max_batches) is not int or max_batches < 1:
        raise KiteDataError("max_batches must be a positive integer")
    if not math.isfinite(max_jitter_s) or max_jitter_s < 0:
        raise KiteDataError("max_jitter_s must be finite and non-negative")
    if not callable(jitter_source) or not callable(sleeper):
        raise KiteDataError("jitter and sleep providers must be callable")
    requested = tuple(symbols)
    if not requested or any(not isinstance(symbol, str) or not symbol.strip() for symbol in requested):
        raise KiteDataError("quote snapshot requires non-empty symbols")
    if len(requested) != len(set(requested)):
        raise KiteDataError("quote snapshot symbols must be unique")
    batch_count = (len(requested) + MAX_QUOTE_SYMBOLS - 1) // MAX_QUOTE_SYMBOLS
    if batch_count > max_batches:
        raise KiteDataError(
            f"quote snapshot exceeds bounded backlog: {batch_count} batches requested, limit is {max_batches}")

    quotes: dict[str, dict] = {}
    unexpected: set[str] = set()
    for batch_index in range(batch_count):
        start = batch_index * MAX_QUOTE_SYMBOLS
        batch = list(requested[start:start + MAX_QUOTE_SYMBOLS])
        if batch_index and max_jitter_s:
            delay = jitter_source(0.0, max_jitter_s)
            if not math.isfinite(delay) or delay < 0 or delay > max_jitter_s:
                raise KiteDataError("jitter provider returned a delay outside its configured bounds")
            sleeper(delay)
        response = quote(batch, kind=kind)
        if not isinstance(response, dict):
            raise KiteDataError("quote endpoint returned a non-object response")
        requested_batch = set(batch)
        unexpected.update(str(symbol) for symbol in response if symbol not in requested_batch)
        for symbol in batch:
            row = response.get(symbol)
            if isinstance(row, dict):
                quotes[symbol] = row
    observed = tuple(symbol for symbol in requested if symbol in quotes)
    missing = tuple(symbol for symbol in requested if symbol not in quotes)
    return QuoteSnapshot(requested, observed, missing, tuple(sorted(unexpected)), batch_count, quotes)


def quote_url(symbols: list[str], kind: str = "ohlc") -> str:
    """The quote URL, exposed so the repeated ``i=`` parameter is testable."""
    paths = {"ltp": "/quote/ltp", "ohlc": "/quote/ohlc", "full": "/quote"}
    query = "&".join(f"i={urllib.parse.quote(s)}" for s in symbols)
    return f"{API}{paths[kind]}?{query}"
