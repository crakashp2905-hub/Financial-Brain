"""Liquidity- and cost-screened opening-range ETF research candidates.

This is a frozen, candidate-only screen. Cost estimates are conservative
diagnostics from observed quotes plus a caller-supplied cost schedule; they
are not calibrated fills and do not demonstrate net alpha.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from financial_brain.features.etf_liquidity import (
    ETFDailyLiquidityObservation,
    analyze_etf_liquidity,
)
from financial_brain.forward_edge.market_events import MarketEvent

STRATEGY_ID = "ETF_INTRADAY_OPENING_RANGE_LIQUIDITY_SCREEN"
STRATEGY_VERSION = "1.0.0"
OPENING_RANGE_BARS = 6
BAR_INTERVAL_MINUTES = 5
MIN_EXTENSION_TO_COST_MULTIPLE = 2.0
SPEC = {
    "strategy_id": STRATEGY_ID,
    "version": STRATEGY_VERSION,
    "signal": "last_complete_5m_close_crosses_first_six_complete_5m_bars_high_or_low",
    "opening_range_bars": OPENING_RANGE_BARS,
    "bar_interval_minutes": BAR_INTERVAL_MINUTES,
    "missing_bar_policy": "reject_candidate",
    "point_in_time_policy": "bar_availability_and_quote_availability_no_later_than_as_of",
    "liquidity_gate": "analyze_etf_liquidity_limits_and_two_sided_top_depth",
    "observed_extension_to_estimated_cost_multiple": MIN_EXTENSION_TO_COST_MULTIPLE,
    "cost_model": "full_observed_spread_for_round_trip_plus_Amihud_size_proxy_plus_caller_supplied_round_trip_costs",
    "execution": "none_candidate_only",
}
SPEC_SHA256 = hashlib.sha256(json.dumps(SPEC, sort_keys=True, separators=(",", ":"),
                                      allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class ETFIntradayScreenPolicy:
    intended_notional_inr: float
    other_round_trip_cost_bps: float
    max_quote_age: timedelta
    max_session_gap_fraction: float
    max_stale_quote_fraction: float
    max_participation_fraction: float
    max_estimated_all_in_cost_bps: float
    min_top_depth_multiple: float = 1.0

    def __post_init__(self) -> None:
        vals = (self.intended_notional_inr, self.other_round_trip_cost_bps,
                self.max_session_gap_fraction, self.max_stale_quote_fraction,
                self.max_participation_fraction, self.max_estimated_all_in_cost_bps,
                self.min_top_depth_multiple)
        if any(not math.isfinite(value) for value in vals):
            raise ValueError("intraday liquidity policy values must be finite")
        if self.intended_notional_inr <= 0 or self.other_round_trip_cost_bps < 0:
            raise ValueError("intended notional must be positive and other costs non-negative")
        if self.max_quote_age < timedelta(0) or self.min_top_depth_multiple <= 0:
            raise ValueError("quote age must be non-negative and top-depth multiple positive")
        if any(not 0 <= value <= 1 for value in (self.max_session_gap_fraction,
                                                  self.max_stale_quote_fraction,
                                                  self.max_participation_fraction)):
            raise ValueError("gap, stale and participation fractions must be between zero and one")
        if self.max_estimated_all_in_cost_bps < 0:
            raise ValueError("maximum estimated cost cannot be negative")


@dataclass(frozen=True, slots=True)
class ETFIntradayCandidate:
    venue: str
    instrument_id: str
    session_date: date
    as_of: datetime
    direction: str | None
    status: str
    reasons: tuple[str, ...]
    opening_range_high: float
    opening_range_low: float
    observed_extension_bps: float | None
    estimated_all_in_round_trip_cost_bps: float | None
    median_top_bid_depth_inr: float | None
    median_top_ask_depth_inr: float | None
    liquidity_report: Mapping[str, Any]
    input_sha256: str
    strategy_id: str = STRATEGY_ID
    strategy_version: str = STRATEGY_VERSION
    strategy_spec_sha256: str = SPEC_SHA256
    candidate_only: bool = True
    orders_authorized: bool = False


def screen_opening_range_candidate(
    *,
    bars: Sequence[Mapping[str, Any]],
    venue: str,
    instrument_id: str,
    session_date: date,
    session_open: datetime,
    session_close: datetime,
    as_of: datetime,
    daily: Sequence[ETFDailyLiquidityObservation],
    quotes: Sequence[MarketEvent],
    expected_sessions: Sequence[date],
    policy: ETFIntradayScreenPolicy,
) -> ETFIntradayCandidate:
    """Apply a point-in-time breakout rule only after spread/depth/cost screening.

    The observed breakout extension must be at least twice the estimated
    round-trip cost proxy to survive this coarse plausibility gate. That
    observed extension is not a forecast of the next return or an alpha claim.
    """
    if (as_of.tzinfo is None or as_of.utcoffset() is None
            or session_open.tzinfo is None or session_open.utcoffset() is None
            or session_close.tzinfo is None or session_close.utcoffset() is None):
        raise ValueError("as_of and session bounds must be timezone aware")
    venue = venue.strip().upper()
    if venue not in {"NSE", "BSE"} or not instrument_id.startswith(f"{venue}:"):
        raise ValueError("instrument identity must be qualified by NSE or BSE venue")
    cutoff = as_of.astimezone(timezone.utc)
    opening = session_open.astimezone(timezone.utc)
    closing = session_close.astimezone(timezone.utc)
    if opening.date() != session_date or opening > cutoff or closing <= opening:
        raise ValueError("session bounds must match the session date and precede as_of")

    clean_bars: list[dict[str, Any]] = []
    prior_start: datetime | None = None
    for row in bars:
        if (row.get("venue") != venue or row.get("instrument_id") != instrument_id
                or row.get("session_date") != session_date.isoformat()
                or row.get("interval_minutes") != BAR_INTERVAL_MINUTES):
            raise ValueError("bars must share the requested instrument, session, and five-minute interval")
        try:
            start = datetime.fromisoformat(str(row["bucket_start"]))
            available = datetime.fromisoformat(str(row["available_at"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("bar timestamps must be ISO-8601") from exc
        if start.tzinfo is None or start.utcoffset() is None or available.tzinfo is None or available.utcoffset() is None:
            raise ValueError("bar timestamps must be timezone aware")
        start, available = start.astimezone(timezone.utc), available.astimezone(timezone.utc)
        if available > cutoff or start + timedelta(minutes=BAR_INTERVAL_MINUTES) > cutoff:
            raise ValueError("bar was not complete and available at as_of")
        if start < opening or start + timedelta(minutes=BAR_INTERVAL_MINUTES) > closing:
            raise ValueError("bar must fall inside the sourced session bounds")
        if prior_start is None:
            if start != opening:
                raise ValueError("bars must begin at the sourced session open")
        elif start - prior_start != timedelta(minutes=BAR_INTERVAL_MINUTES):
            raise ValueError("missing or duplicated five-minute bars reject the candidate")
        for field in ("open", "high", "low", "close"):
            value = row.get(field)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError("bar prices must be finite and positive")
        if row["high"] < max(row["open"], row["close"], row["low"]) \
                or row["low"] > min(row["open"], row["close"], row["high"]):
            raise ValueError("bar OHLC is inconsistent")
        digest = row.get("input_sha256")
        event_ids = row.get("source_event_ids")
        if (not isinstance(digest, str) or len(digest) != 64
                or any(char not in "0123456789abcdef" for char in digest)
                or not isinstance(event_ids, list) or not event_ids):
            raise ValueError("every bar needs source event IDs and a lowercase SHA-256")
        clean_bars.append({**row, "bucket_start_utc": start, "available_at_utc": available})
        prior_start = start

    if len(clean_bars) <= OPENING_RANGE_BARS:
        raise ValueError("at least one complete bar after the six-bar opening range is required")
    range_rows = clean_bars[:OPENING_RANGE_BARS]
    if any(row["available_at_utc"] > cutoff for row in range_rows):
        raise ValueError("opening range was not available at as_of")
    range_high = max(float(row["high"]) for row in range_rows)
    range_low = min(float(row["low"]) for row in range_rows)

    eligible_quotes = [row for row in quotes if row.venue == venue and row.instrument_id == instrument_id
                       and row.available_at.astimezone(timezone.utc) <= cutoff]
    liquidity = analyze_etf_liquidity(
        daily, eligible_quotes, expected_sessions=expected_sessions,
        venue=venue, instrument_id=instrument_id,
        intended_notional_inr=policy.intended_notional_inr,
        max_quote_age=policy.max_quote_age,
        max_session_gap_fraction=policy.max_session_gap_fraction,
        max_stale_quote_fraction=policy.max_stale_quote_fraction,
        max_participation_fraction=policy.max_participation_fraction,
        max_estimated_all_in_cost_bps=policy.max_estimated_all_in_cost_bps,
        other_round_trip_cost_bps=policy.other_round_trip_cost_bps,
    )
    last, previous = clean_bars[-1], clean_bars[-2]
    direction = None
    extension_bps = None
    if float(last["close"]) > range_high and float(previous["close"]) <= range_high:
        direction = "LONG"
        extension_bps = (float(last["close"]) / range_high - 1) * 10000
    elif float(last["close"]) < range_low and float(previous["close"]) >= range_low:
        direction = "SHORT"
        extension_bps = (range_low / float(last["close"]) - 1) * 10000

    reasons: list[str] = []
    if liquidity["status"] != "ESTIMATE" or not liquidity["limits_met"]:
        reasons.append("LIQUIDITY_OR_COST_LIMIT_NOT_MET")
    bid_depth = liquidity["median_top_bid_depth_inr"]
    ask_depth = liquidity["median_top_ask_depth_inr"]
    min_depth = policy.intended_notional_inr * policy.min_top_depth_multiple
    if bid_depth is None or ask_depth is None or min(bid_depth, ask_depth) < min_depth:
        reasons.append("TWO_SIDED_TOP_DEPTH_INSUFFICIENT")
    estimated_cost = liquidity["estimated_all_in_round_trip_cost_bps"]
    if direction is None:
        reasons.append("NO_OPENING_RANGE_BREAKOUT_AT_AS_OF")
    elif estimated_cost is None or extension_bps is None \
            or extension_bps < MIN_EXTENSION_TO_COST_MULTIPLE * estimated_cost:
        reasons.append("OBSERVED_EXTENSION_BELOW_COST_PLAUSIBILITY_GATE")
    status = "ELIGIBLE_CANDIDATE" if not reasons else "REJECTED_OR_NO_SIGNAL"
    if reasons:
        direction = None

    payload = {
        "spec_sha256": SPEC_SHA256,
        "as_of": cutoff.isoformat(),
        "bars": [(row["bucket_start_utc"].isoformat(), row["input_sha256"]) for row in clean_bars],
        "quote_event_sha256": sorted(row.sha256() for row in eligible_quotes),
        "daily_sources": sorted((row.session_date.isoformat(), row.source_sha256) for row in daily),
        "policy": {key: str(value) for key, value in policy.__dict__.items()} if hasattr(policy, "__dict__")
                  else {field: str(getattr(policy, field)) for field in policy.__dataclass_fields__},
        "liquidity_report": liquidity,
        "direction": direction,
        "reasons": reasons,
    }
    input_digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                             allow_nan=False, default=str).encode()).hexdigest()
    return ETFIntradayCandidate(
        venue=venue, instrument_id=instrument_id, session_date=session_date, as_of=cutoff,
        direction=direction, status=status, reasons=tuple(reasons),
        opening_range_high=range_high, opening_range_low=range_low,
        observed_extension_bps=extension_bps,
        estimated_all_in_round_trip_cost_bps=estimated_cost,
        median_top_bid_depth_inr=bid_depth, median_top_ask_depth_inr=ask_depth,
        liquidity_report=liquidity, input_sha256=input_digest,
    )


__all__ = ["ETFIntradayCandidate", "ETFIntradayScreenPolicy", "SPEC", "SPEC_SHA256",
           "STRATEGY_ID", "STRATEGY_VERSION", "screen_opening_range_candidate"]
