"""Quote-quality-gated, single-as-of implied-volatility surface snapshots.

This is a research diagnostic, not a live option recommendation. It refuses stale,
future, crossed, wide, mismatched, or no-arbitrage-inconsistent quote sets. The
implemented static checks cover European call bounds, strike monotonicity and
butterfly convexity; calendar-spread arbitrage is intentionally not inferred.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TypedDict

from financial_brain.kernel.options import OptionError, greeks, implied_vol


class OptionSurfaceError(ValueError):
    """Invalid option snapshot, quote, or static arbitrage diagnostic."""


@dataclass(frozen=True, slots=True)
class OptionSurfaceQuote:
    quote_id: str
    source_id: str
    source_sha256: str
    underlying_id: str
    instrument_id: str
    expiry_at: datetime
    strike: float
    option_type: str
    observed_at: datetime
    available_at: datetime
    bid: float
    ask: float
    bid_quantity: int
    ask_quantity: int


@dataclass(frozen=True, slots=True)
class OptionSurfaceRequest:
    underlying_id: str
    spot: float
    rate: float
    dividend_yield: float
    as_of: datetime
    max_quote_age_seconds: float
    max_spread_bps: float
    quotes: tuple[OptionSurfaceQuote, ...]


class _QuotePoint(TypedDict):
    call_mid: float
    iv: float
    greeks: dict[str, float]
    quote_id: str
    source_id: str
    source_sha256: str
    instrument_id: str
    observed_at: str
    available_at: str
    spread_bps: float


def _aware_utc(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise OptionSurfaceError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def build_option_surface(request: OptionSurfaceRequest) -> dict[str, object]:
    """Build immutable IV/Greek points from one decision-time, source-hashed snapshot."""
    if not isinstance(request, OptionSurfaceRequest) or not request.quotes:
        raise OptionSurfaceError("a typed request with option quotes is required")
    as_of = _aware_utc(request.as_of, "as_of")
    if not request.underlying_id.strip():
        raise OptionSurfaceError("underlying_id is required")
    if (not all(math.isfinite(x) for x in (request.spot, request.rate, request.dividend_yield,
                                            request.max_quote_age_seconds, request.max_spread_bps))
            or request.spot <= 0 or request.max_quote_age_seconds < 0 or request.max_spread_bps <= 0):
        raise OptionSurfaceError("spot, rate, yield and quote thresholds must be finite and valid")
    if len({quote.quote_id for quote in request.quotes}) != len(request.quotes):
        raise OptionSurfaceError("quote IDs must be unique in a snapshot")

    calls: dict[datetime, dict[float, dict[str, _QuotePoint]]] = {}
    for quote in request.quotes:
        expiry = _aware_utc(quote.expiry_at, "expiry_at")
        observed = _aware_utc(quote.observed_at, "observed_at")
        available = _aware_utc(quote.available_at, "available_at")
        if not all(isinstance(value, str) and value.strip() for value in
                   (quote.quote_id, quote.source_id, quote.underlying_id, quote.instrument_id)):
            raise OptionSurfaceError("quote identity and source are required")
        if (len(quote.source_sha256) != 64 or any(c not in "0123456789abcdef" for c in quote.source_sha256)):
            raise OptionSurfaceError("source_sha256 must be lowercase SHA-256")
        if quote.underlying_id != request.underlying_id:
            raise OptionSurfaceError("quote underlying differs from requested surface")
        if quote.option_type not in {"CE", "PE"} or not math.isfinite(quote.strike) or quote.strike <= 0:
            raise OptionSurfaceError("option type and positive finite strike are required")
        if expiry <= as_of or observed > as_of or available > as_of or available < observed:
            raise OptionSurfaceError("quote is expired, future-dated, or unavailable at snapshot time")
        age = (as_of - observed).total_seconds()
        if age > request.max_quote_age_seconds:
            raise OptionSurfaceError("option quote is stale")
        values = (quote.bid, quote.ask)
        if (not all(math.isfinite(x) for x in values) or quote.bid <= 0 or quote.ask < quote.bid
                or type(quote.bid_quantity) is not int or type(quote.ask_quantity) is not int
                or quote.bid_quantity <= 0 or quote.ask_quantity <= 0):
            raise OptionSurfaceError("positive, uncrossed bid/ask and displayed quantities are required")
        mid = (quote.bid + quote.ask) / 2
        spread_bps = (quote.ask - quote.bid) / mid * 10_000
        if spread_bps > request.max_spread_bps:
            raise OptionSurfaceError("option quote spread exceeds the frozen quality threshold")
        t = (expiry - as_of).total_seconds() / (365.0 * 24 * 60 * 60)
        try:
            iv = implied_vol(mid, request.spot, quote.strike, t, request.rate,
                             quote.option_type == "CE", request.dividend_yield)
            point_greeks = greeks(request.spot, quote.strike, t, request.rate, iv,
                                  quote.option_type == "CE", request.dividend_yield)
            call_mid = (mid if quote.option_type == "CE" else
                        mid + request.spot * math.exp(-request.dividend_yield * t)
                        - quote.strike * math.exp(-request.rate * t))
        except (OptionError, ValueError, OverflowError) as exc:
            raise OptionSurfaceError(f"quote failed no-arbitrage/IV validation: {quote.quote_id}") from exc
        if call_mid < -1e-9:
            raise OptionSurfaceError("put-call parity implies a negative call price")
        strike_rows = calls.setdefault(expiry, {})
        row = strike_rows.setdefault(quote.strike, {})
        if quote.option_type in row:
            raise OptionSurfaceError("duplicate option side for expiry and strike")
        row[quote.option_type] = {
            "call_mid": call_mid, "iv": iv, "greeks": point_greeks,
            "quote_id": quote.quote_id, "source_id": quote.source_id,
            "source_sha256": quote.source_sha256,
            "instrument_id": quote.instrument_id,
            "observed_at": observed.isoformat(), "available_at": available.isoformat(),
            "spread_bps": spread_bps,
        }

    points: list[dict[str, object]] = []
    diagnostics: list[dict[str, object]] = []
    for expiry in sorted(calls):
        strikes = calls[expiry]
        if len(strikes) < 3:
            raise OptionSurfaceError("each expiry needs at least three distinct strikes for convexity checks")
        rows: list[tuple[float, float, _QuotePoint]] = []
        for strike in sorted(strikes):
            side_rows = strikes[strike]
            if "CE" in side_rows:
                chosen = side_rows["CE"]
                if "PE" in side_rows:
                    put = side_rows["PE"]
                    if abs(chosen["call_mid"] - put["call_mid"]) > (
                            (request.max_spread_bps / 10_000) * max(chosen["call_mid"], 1.0)):
                        raise OptionSurfaceError("call/put midpoint parity disagreement exceeds tolerance")
            elif "PE" in side_rows:
                chosen = side_rows["PE"]
            else:
                raise OptionSurfaceError("strike has no usable call or put quote")
            price = float(chosen["call_mid"])
            rows.append((strike, price, chosen))
        slopes = []
        for (k0, c0, _), (k1, c1, _) in zip(rows, rows[1:]):
            if c1 > c0 + 1e-8:
                raise OptionSurfaceError("call prices increase with strike (monotonicity violation)")
            slopes.append((c1 - c0) / (k1 - k0))
        if any(right < left - 1e-8 for left, right in zip(slopes, slopes[1:])):
            raise OptionSurfaceError("call prices are not convex across strike (butterfly violation)")
        diagnostics.append({"expiry_at": expiry.isoformat(), "strike_count": len(rows),
                            "call_monotonicity": "PASS", "butterfly_convexity": "PASS"})
        for strike, call_mid, _chosen in rows:
            side_rows = strikes[strike]
            if "CE" in side_rows:
                iv_source = side_rows["CE"]
                iv = float(iv_source["iv"])
                greek_values = iv_source["greeks"]
            else:
                iv_source = side_rows["PE"]
                iv = float(iv_source["iv"])
                greek_values = iv_source["greeks"]
            points.append({"expiry_at": expiry.isoformat(), "strike": strike,
                           "call_equivalent_mid": call_mid, "implied_volatility": iv,
                           "greeks": greek_values, "source_quote_id": iv_source["quote_id"],
                           "source_id": iv_source["source_id"],
                           "source_sha256": iv_source["source_sha256"],
                           "instrument_id": iv_source["instrument_id"],
                           "spread_bps": iv_source["spread_bps"]})

    body: dict[str, object] = {
        "schema": "fb.option-iv-surface.v1", "underlying_id": request.underlying_id,
        "spot": request.spot, "rate": request.rate, "dividend_yield": request.dividend_yield,
        "as_of": as_of.isoformat(), "quality_gate": "PASS",
        "arbitrage_checks": diagnostics,
        "calendar_spread_arbitrage": "NOT_TESTED",
        "points": points,
    }
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False)
    body["snapshot_sha256"] = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    return body
