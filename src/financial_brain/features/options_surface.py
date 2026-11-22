"""Point-in-time option volatility and open-interest features from observed books.

The builder accepts only synchronized, fresh, two-sided option quotes and a timestamped
underlying observation. It uses the option midpoint, never last traded price, for IV and
Greeks. It reports only quoted contracts and expiry summaries; no strike or expiry values
are interpolated or extrapolated. Missing OI remains missing.
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from ..kernel.options import OptionError, greeks, implied_vol

IST = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc
YEAR_SECONDS = 365.25 * 24 * 60 * 60


class OptionSurfaceError(ValueError):
    """Invalid decision clock or surface configuration."""


def _aware(value: Any, field: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise OptionSurfaceError(f"{field} must be a timezone-aware datetime")
    return value.astimezone(UTC)


def _expiry(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise OptionSurfaceError("expiry must be an ISO date") from exc


def _finite_positive(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) and out > 0 else None


def build_surface(
    observations: list[dict[str, Any]],
    *,
    as_of: datetime,
    rate: float,
    dividend_yield: float = 0.0,
    max_quote_age: timedelta = timedelta(seconds=120),
    max_spot_skew: timedelta = timedelta(seconds=30),
    delta_tolerance: float = 0.15,
) -> dict[str, Any]:
    """Build contract IV/Greeks and per-expiry ATM/skew/OI features.

    Required observation keys are ``underlying``, ``expiry``, ``strike``,
    ``option_type`` (CE/PE or CALL/PUT), ``bid``, ``ask``, ``underlying_ltp``,
    ``received_at``, ``source_at`` and ``underlying_source_at``. ``open_interest`` may be
    absent; then the IV/Greek point remains usable while its OI feature stays null.
    All clocks must be timezone-aware. A point is omitted if the option book is stale,
    crossed, source-dated after receipt, or not synchronized with its underlying quote.
    """
    decision = _aware(as_of, "as_of")
    assert decision is not None
    if rate <= -1 or not math.isfinite(rate) or not math.isfinite(dividend_yield):
        raise OptionSurfaceError("rate and dividend_yield must be finite; rate must exceed -1")
    if max_quote_age < timedelta(0) or max_spot_skew < timedelta(0):
        raise OptionSurfaceError("quote age and spot skew tolerances must be non-negative")
    if not 0 < delta_tolerance <= 0.5:
        raise OptionSurfaceError("delta_tolerance must be in (0, 0.5]")

    accepted: list[dict[str, Any]] = []
    rejected: defaultdict[str, int] = defaultdict(int)
    # Collapse repeated archive polls to the latest observed row per listed contract.
    # Quality checks happen after this selection, so a bad latest book cannot silently
    # fall back to an older, apparently tradable quote.
    latest: dict[tuple[str, str, str, str], tuple[datetime, dict[str, Any]]] = {}
    for row in observations:
        try:
            received = _aware(row.get("received_at"), "received_at")
        except OptionSurfaceError:
            rejected["INVALID_CLOCK"] += 1
            continue
        if received is None:
            rejected["MISSING_SOURCE_CLOCK"] += 1
            continue
        if received > decision:
            rejected["FUTURE_OR_INVERTED_CLOCK"] += 1
            continue
        key = (str(row.get("underlying", "")), str(row.get("expiry", "")),
               str(row.get("strike", "")), str(row.get("option_type", "")).upper())
        if key not in latest or received > latest[key][0]:
            latest[key] = (received, row)

    for received, row in latest.values():
        try:
            source = _aware(row.get("source_at"), "source_at")
            spot_at = _aware(row.get("underlying_source_at"), "underlying_source_at")
        except OptionSurfaceError:
            rejected["INVALID_CLOCK"] += 1
            continue
        if source is None or spot_at is None:
            rejected["MISSING_SOURCE_CLOCK"] += 1
            continue
        if source > received or spot_at > received:
            rejected["FUTURE_OR_INVERTED_CLOCK"] += 1
            continue
        if decision - received > max_quote_age:
            rejected["STALE_OPTION_QUOTE"] += 1
            continue
        if abs(source - spot_at) > max_spot_skew:
            rejected["UNSYNCHRONIZED_SPOT"] += 1
            continue

        bid, ask = _finite_positive(row.get("bid")), _finite_positive(row.get("ask"))
        spot = _finite_positive(row.get("underlying_ltp"))
        strike = _finite_positive(row.get("strike"))
        if bid is None or ask is None or ask < bid:
            rejected["NO_VALID_TWO_SIDED_BOOK"] += 1
            continue
        if spot is None or strike is None:
            rejected["MISSING_SPOT_OR_STRIKE"] += 1
            continue
        kind = str(row.get("option_type", "")).upper()
        if kind in {"CALL", "CE"}:
            is_call, kind = True, "CE"
        elif kind in {"PUT", "PE"}:
            is_call, kind = False, "PE"
        else:
            rejected["UNKNOWN_OPTION_TYPE"] += 1
            continue
        try:
            expiry_date = _expiry(row.get("expiry"))
        except OptionSurfaceError:
            rejected["INVALID_EXPIRY"] += 1
            continue
        expiry_close = datetime.combine(expiry_date, time(15, 30), tzinfo=IST).astimezone(UTC)
        years = (expiry_close - decision).total_seconds() / YEAR_SECONDS
        if years <= 0:
            rejected["EXPIRED_CONTRACT"] += 1
            continue

        midpoint = (bid + ask) / 2.0
        try:
            iv = implied_vol(midpoint, spot, strike, years, rate, is_call, dividend_yield)
            risk = greeks(spot, strike, years, rate, iv, is_call, dividend_yield)
        except (OptionError, ValueError, OverflowError):
            rejected["UNESTIMABLE_IV_OR_GREEKS"] += 1
            continue
        oi = row.get("open_interest")
        if oi is not None:
            try:
                oi = int(oi)
                if oi < 0:
                    oi = None
            except (TypeError, ValueError, OverflowError):
                oi = None
        accepted.append({
            "underlying": str(row.get("underlying", "")),
            "expiry": expiry_date.isoformat(),
            "strike": strike,
            "option_type": kind,
            "bid": bid,
            "ask": ask,
            "midpoint": midpoint,
            "underlying_ltp": spot,
            "received_at": received.isoformat(),
            "source_at": source.isoformat(),
            "underlying_source_at": spot_at.isoformat(),
            "open_interest": oi,
            "implied_volatility": iv,
            "delta": risk["delta"],
            "gamma": risk["gamma"],
            "vega": risk["vega"],
            "theta": risk["theta"],
            "moneyness": strike / spot - 1.0,
            "years_to_expiry": years,
        })

    by_expiry: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for point in accepted:
        by_expiry[point["expiry"]].append(point)
    expiries = []
    for expiry_text, points in sorted(by_expiry.items()):
        calls = [p for p in points if p["option_type"] == "CE"]
        puts = [p for p in points if p["option_type"] == "PE"]
        atm_call = min(calls, key=lambda p: abs(p["delta"] - 0.5), default=None)
        atm_put = min(puts, key=lambda p: abs(abs(p["delta"]) - 0.5), default=None)
        put25 = min(puts, key=lambda p: abs(abs(p["delta"]) - 0.25), default=None)
        call25 = min(calls, key=lambda p: abs(p["delta"] - 0.25), default=None)
        atm_values = [p["implied_volatility"] for p in (atm_call, atm_put)
                      if p is not None and abs(abs(p["delta"]) - 0.5) <= delta_tolerance]
        put25_iv = put25["implied_volatility"] if put25 and abs(abs(put25["delta"]) - 0.25) <= delta_tolerance else None
        call25_iv = call25["implied_volatility"] if call25 and abs(call25["delta"] - 0.25) <= delta_tolerance else None
        call_oi = sum(p["open_interest"] for p in calls if p["open_interest"] is not None)
        put_oi = sum(p["open_interest"] for p in puts if p["open_interest"] is not None)
        oi_known = any(p["open_interest"] is not None for p in points)
        expiries.append({
            "expiry": expiry_text,
            "contracts": len(points),
            "tenor_years": points[0]["years_to_expiry"],
            "atm_iv": sum(atm_values) / len(atm_values) if atm_values else None,
            "put_25delta_iv": put25_iv,
            "call_25delta_iv": call25_iv,
            "risk_reversal_25delta": put25_iv - call25_iv if put25_iv is not None and call25_iv is not None else None,
            "call_open_interest": call_oi if oi_known else None,
            "put_open_interest": put_oi if oi_known else None,
            "put_call_oi_ratio": put_oi / call_oi if oi_known and call_oi > 0 else None,
        })

    return {
        "status": "AVAILABLE" if accepted else "UNAVAILABLE",
        "as_of": decision.isoformat(),
        "rate": rate,
        "dividend_yield": dividend_yield,
        "contracts": accepted,
        "expiries": expiries,
        "atm_iv_term_structure": [
            {"expiry": point["expiry"], "tenor_years": point["tenor_years"],
             "atm_iv": point["atm_iv"]}
            for point in expiries if point["atm_iv"] is not None
        ],
        "rejected_counts": dict(sorted(rejected.items())),
        "method": "observed two-sided midpoint; European BSM; no strike/expiry interpolation or extrapolation",
    }


def build_from_archive(
    con: Any,
    *,
    as_of: datetime,
    rate: float,
    dividend_yield: float = 0.0,
    max_quote_age: timedelta = timedelta(seconds=120),
    max_spot_skew: timedelta = timedelta(seconds=30),
    delta_tolerance: float = 0.15,
) -> dict[str, Any]:
    """Build a surface from a DuckDB option quote archive at an explicit decision time."""
    required = {
        "underlying", "expiry", "strike", "option_type", "bid", "ask", "underlying_ltp",
        "open_interest", "received_at", "source_at", "underlying_source_at",
    }
    exists = con.execute("""SELECT 1 FROM information_schema.tables
                           WHERE table_name = 'option_quotes'""").fetchone()
    if not exists:
        return {"status": "UNAVAILABLE", "reason": "OPTION_QUOTE_ARCHIVE_MISSING",
                "contracts": [], "expiries": []}
    columns = {row[0] for row in con.execute("""SELECT column_name FROM information_schema.columns
                                               WHERE table_name = 'option_quotes'""").fetchall()}
    missing = sorted(required - columns)
    if missing:
        return {"status": "UNAVAILABLE", "reason": "OPTION_ARCHIVE_SCHEMA_INCOMPLETE",
                "missing_columns": missing, "contracts": [], "expiries": []}
    decision = _aware(as_of, "as_of")
    assert decision is not None
    start = decision - max_quote_age
    rows = con.execute("""SELECT underlying, expiry, strike, option_type, bid, ask,
                                 underlying_ltp, open_interest, received_at, source_at,
                                 underlying_source_at
                          FROM option_quotes
                          WHERE received_at >= ? AND received_at <= ?
                          ORDER BY received_at""", [start, decision]).fetchall()
    observations = [dict(zip(
        ("underlying", "expiry", "strike", "option_type", "bid", "ask", "underlying_ltp",
         "open_interest", "received_at", "source_at", "underlying_source_at"), row, strict=True
    )) for row in rows]
    return build_surface(observations, as_of=as_of, rate=rate, dividend_yield=dividend_yield,
                         max_quote_age=max_quote_age, max_spot_skew=max_spot_skew,
                         delta_tolerance=delta_tolerance)


__all__ = ["OptionSurfaceError", "build_from_archive", "build_surface"]
