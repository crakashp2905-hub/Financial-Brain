"""Point-in-time ETF desk panel assembled from sourced quote and reference evidence."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from financial_brain.features.etf_valuation import compare_quote_to_inav
from financial_brain.forward_edge.market_data import executable_quote
from financial_brain.forward_edge.market_events import EventKind, MarketEvent
from financial_brain.providers.etf_reference import (
    ETFReferenceError,
    ETFReferenceRecord,
    ETFTrackingMetrics,
    ETFValuationObservation,
)


def build_etf_trader_panel(
    *,
    identity: ETFReferenceRecord,
    quote: MarketEvent | None,
    inav: ETFValuationObservation | None,
    tracking: ETFTrackingMetrics | None,
    liquidity: dict[str, Any] | None,
    as_of: datetime,
    max_quote_age: timedelta,
    max_inav_age: timedelta,
    max_quote_inav_skew: timedelta,
    max_tracking_age: timedelta,
) -> dict[str, Any]:
    """Build an immutable-style, read-only display model; missing or stale evidence stays explicit."""
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ETFReferenceError("panel as_of must be timezone aware")
    windows = (max_quote_age, max_inav_age, max_quote_inav_skew, max_tracking_age)
    if any(not isinstance(window, timedelta) or window < timedelta(0) for window in windows):
        raise ETFReferenceError("panel freshness windows must be non-negative")
    as_of_utc = as_of.astimezone(timezone.utc)
    exchange_day = as_of.astimezone(ZoneInfo("Asia/Kolkata")).date()
    expected_instrument = f"{identity.exchange}:{identity.symbol}"

    quote_row: dict[str, Any] = {"status": "MISSING", "bid": None, "ask": None,
                                 "spread_bps": None, "source": None, "event_time": None,
                                 "received_at": None, "available_at": None, "age_seconds": None,
                                 "sha256": None}
    if quote is not None:
        if (quote.kind is not EventKind.QUOTE or quote.venue != identity.exchange
                or quote.instrument_id != expected_instrument):
            raise ETFReferenceError("quote must identify the same listed ETF")
        executable = executable_quote(quote)
        if executable is None:
            quote_row["status"] = "NON_EXECUTABLE"
        elif quote.available_at > as_of_utc:
            quote_row["status"] = "NOT_YET_AVAILABLE"
        else:
            bid, ask = Decimal(str(executable["bid"])), Decimal(str(executable["ask"]))
            age = as_of_utc - quote.event_time
            quote_row.update({
                "status": "FRESH" if timedelta(0) <= age <= max_quote_age else "STALE",
                "bid": str(bid), "ask": str(ask),
                "spread_bps": str(((ask - bid) / ((ask + bid) / 2) * Decimal(10000)).normalize()),
                "source": quote.source, "event_time": quote.event_time.isoformat(),
                "received_at": quote.received_at.isoformat(),
                "available_at": quote.available_at.isoformat(), "age_seconds": age.total_seconds(),
                "sha256": quote.sha256(),
            })

    valuation: dict[str, Any] = {"status": "MISSING", "premium_discount_bps": None,
                                "midquote": None, "inav": None, "inav_age_seconds": None,
                                "quote_sha256": None, "inav_sha256": None, "reason": "iNAV unavailable"}
    if inav is not None and quote is not None:
        try:
            comparison = compare_quote_to_inav(
                quote, identity=identity, inav=inav, decision_time=as_of,
                quote_max_age=max_quote_age, inav_max_age=max_inav_age,
                max_observation_skew=max_quote_inav_skew,
            )
            valuation = {"status": "FRESH", "premium_discount_bps": comparison["premium_discount_bps"],
                         "midquote": comparison["midquote"], "inav": comparison["inav"],
                         "inav_age_seconds": comparison["inav_age_seconds"],
                         "quote_sha256": comparison["quote_sha256"], "inav_sha256": comparison["inav_sha256"],
                         "reason": None}
        except ETFReferenceError as exc:
            valuation = {**valuation, "status": "UNAVAILABLE", "reason": str(exc)}

    tracking_row: dict[str, Any] = {"status": "MISSING", "tracking_difference_bps": None,
                                   "tracking_error_bps": None, "expense_ratio_bps": None,
                                   "source": None, "source_ref": None, "source_sha256": None,
                                   "reason": "tracking disclosure unavailable"}
    if tracking is not None:
        if (tracking.exchange != identity.exchange or tracking.symbol != identity.symbol
                or tracking.isin != identity.isin):
            raise ETFReferenceError("tracking disclosure must match the same ETF identity")
        age = as_of_utc - tracking.observed_at
        effective = (tracking.effective_from <= exchange_day
                     and (tracking.effective_to is None or exchange_day <= tracking.effective_to))
        available = tracking.published_at <= as_of_utc and tracking.observed_at <= as_of_utc
        if not available:
            status, reason = "UNAVAILABLE", "tracking disclosure was not yet available"
        elif age < timedelta(0) or age > max_tracking_age:
            status, reason = "STALE", "tracking disclosure is stale"
        elif not effective:
            status, reason = "OUT_OF_EFFECTIVE_RANGE", "tracking disclosure is not effective on panel date"
        else:
            status, reason = "FRESH", None
        tracking_row = {
            "status": status,
            "tracking_difference_bps": str(tracking.tracking_difference_bps) if status == "FRESH" else None,
            "tracking_error_bps": str(tracking.tracking_error_bps) if status == "FRESH" else None,
            "expense_ratio_bps": str(tracking.expense_ratio_bps) if status == "FRESH" else None,
            "source": tracking.source_id, "source_ref": tracking.source_ref,
            "source_sha256": tracking.content_sha256(), "reason": reason,
        }

    liquidity_row: dict[str, Any] = {"status": "MISSING", "estimated_all_in_round_trip_cost_bps": None,
                                    "median_spread_bps": None, "capacity_participation_fraction": None,
                                    "cost_model_label": None, "source_sha256": [],
                                    "reason": "liquidity estimate unavailable"}
    if liquidity is not None:
        if liquidity.get("instrument_id") != expected_instrument:
            raise ETFReferenceError("liquidity estimate must match the same ETF instrument")
        hashes = liquidity.get("source_sha256", [])
        if not isinstance(hashes, list) or any(
            not isinstance(value, str) or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value) for value in hashes
        ):
            raise ETFReferenceError("liquidity estimate source hashes are malformed")
        liquidity_row = {
            "status": liquidity.get("status", "UNKNOWN"),
            "estimated_all_in_round_trip_cost_bps": liquidity.get("estimated_all_in_round_trip_cost_bps"),
            "median_spread_bps": liquidity.get("median_spread_bps"),
            "capacity_participation_fraction": liquidity.get("capacity_participation_fraction"),
            "cost_model_label": liquidity.get("cost_model_label"),
            "source_sha256": sorted(set(hashes)),
            "reason": None if liquidity.get("status") == "ESTIMATE" else "insufficient source observations",
        }

    return {
        "schema": "fb.etf_trader_panel.v1", "as_of": as_of_utc.isoformat(),
        "instrument_id": expected_instrument, "isin": identity.isin,
        "underlying_benchmark": identity.underlying_index,
        "quote": quote_row, "valuation": valuation, "tracking": tracking_row,
        "liquidity_and_cost": liquidity_row,
        "display_only": True,
    }


__all__ = ["build_etf_trader_panel"]
