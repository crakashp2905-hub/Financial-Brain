"""Observed-data ETF liquidity and size-cost diagnostics; thresholds are caller-owned."""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta
from statistics import median, stdev
from typing import Any, Iterable

from financial_brain.forward_edge.market_data import executable_quote
from financial_brain.forward_edge.market_events import EventKind, MarketEvent


@dataclass(frozen=True)
class ETFDailyLiquidityObservation:
    session_date: date
    close: float
    turnover_inr: float
    source_sha256: str

    def __post_init__(self) -> None:
        if not math.isfinite(self.close) or self.close <= 0:
            raise ValueError("ETF close must be finite and positive")
        if not math.isfinite(self.turnover_inr) or self.turnover_inr <= 0:
            raise ValueError("ETF turnover must be finite and positive")
        if len(self.source_sha256) != 64 or any(c not in "0123456789abcdef" for c in self.source_sha256):
            raise ValueError("ETF daily observation needs a lowercase SHA-256 source hash")


def analyze_etf_liquidity(
    daily: Iterable[ETFDailyLiquidityObservation],
    quotes: Iterable[MarketEvent],
    *,
    expected_sessions: Iterable[date],
    venue: str,
    instrument_id: str,
    intended_notional_inr: float,
    max_quote_age: timedelta,
    max_session_gap_fraction: float,
    max_stale_quote_fraction: float,
    max_participation_fraction: float,
    max_estimated_all_in_cost_bps: float,
    other_round_trip_cost_bps: float,
) -> dict[str, Any]:
    """Summarize only sourced observations; emit no tradeable verdict on sparse data.

    The impact term is an Amihud-style size proxy, not a calibrated execution model.
    Explicit non-impact round-trip costs must be provided by the active cost schedule.
    """
    venue = venue.strip().upper()
    if venue not in {"NSE", "BSE"} or not instrument_id.startswith(f"{venue}:"):
        raise ValueError("instrument identity must be qualified with the matching NSE/BSE venue")
    numeric = (intended_notional_inr, max_session_gap_fraction, max_stale_quote_fraction,
               max_participation_fraction, max_estimated_all_in_cost_bps, other_round_trip_cost_bps)
    if any(not math.isfinite(value) for value in numeric):
        raise ValueError("liquidity thresholds and order size must be finite")
    if intended_notional_inr <= 0 or other_round_trip_cost_bps < 0 or max_quote_age < timedelta(0):
        raise ValueError("order size must be positive, costs non-negative and quote age non-negative")
    if any(not 0 <= value <= 1 for value in (max_session_gap_fraction, max_stale_quote_fraction,
                                              max_participation_fraction)):
        raise ValueError("gap, stale and participation thresholds must be between zero and one")
    if max_estimated_all_in_cost_bps < 0:
        raise ValueError("maximum estimated cost cannot be negative")

    sessions = sorted(set(expected_sessions))
    observations = sorted(daily, key=lambda row: row.session_date)
    if len({row.session_date for row in observations}) != len(observations):
        raise ValueError("duplicate ETF daily observations require explicit revision resolution first")
    by_day = {row.session_date: row for row in observations}
    missing_sessions = [day.isoformat() for day in sessions if day not in by_day]
    gap_fraction = len(missing_sessions) / len(sessions) if sessions else 1.0
    median_turnover = median([row.turnover_inr for row in observations]) if observations else None

    adjacent_returns: list[tuple[float, float]] = []
    for prior_day, next_day in zip(sessions, sessions[1:]):
        prior, current = by_day.get(prior_day), by_day.get(next_day)
        if prior is None or current is None:
            continue
        ret = math.log(current.close / prior.close)
        adjacent_returns.append((ret, current.turnover_inr))
    return_volatility = stdev(ret for ret, _ in adjacent_returns) * math.sqrt(252) if len(adjacent_returns) >= 2 else None
    amihud_values = [abs(ret) / turnover for ret, turnover in adjacent_returns if turnover > 0]
    amihud_median = median(amihud_values) if amihud_values else None

    valid_quotes: list[tuple[float, float, float]] = []
    quote_source_hashes: set[str] = set()
    stale = 0
    quote_count = 0
    for event in quotes:
        if event.venue != venue or event.instrument_id != instrument_id or event.kind is not EventKind.QUOTE:
            continue
        quote_count += 1
        age = event.received_at - event.event_time
        executable = executable_quote(event)
        if age < timedelta(0) or age > max_quote_age or executable is None:
            stale += 1
            continue
        quote_source_hashes.add(event.sha256())
        bid, ask = float(executable["bid"]), float(executable["ask"])
        mid = (bid + ask) / 2
        spread_bps = (ask - bid) / mid * 10000
        bid_depth_inr = bid * int(executable["bid_quantity"])
        ask_depth_inr = ask * int(executable["ask_quantity"])
        valid_quotes.append((spread_bps, bid_depth_inr, ask_depth_inr))

    stale_fraction = stale / quote_count if quote_count else 1.0
    median_spread = median([row[0] for row in valid_quotes]) if valid_quotes else None
    median_bid_depth = median([row[1] for row in valid_quotes]) if valid_quotes else None
    median_ask_depth = median([row[2] for row in valid_quotes]) if valid_quotes else None
    capacity_fraction = intended_notional_inr / median_turnover if median_turnover else None
    impact_bps = (amihud_median * intended_notional_inr * 10000) if amihud_median is not None else None
    estimated_cost = ((median_spread / 2) + impact_bps + other_round_trip_cost_bps
                      if median_spread is not None and impact_bps is not None else None)
    sufficient = bool(sessions and observations and adjacent_returns and valid_quotes)
    limits_met = bool(
        sufficient and estimated_cost is not None and capacity_fraction is not None
        and gap_fraction <= max_session_gap_fraction
        and stale_fraction <= max_stale_quote_fraction
        and capacity_fraction <= max_participation_fraction
        and estimated_cost <= max_estimated_all_in_cost_bps
    )
    return {
        "status": "ESTIMATE" if sufficient else "INSUFFICIENT_DATA",
        "venue": venue,
        "instrument_id": instrument_id,
        "expected_sessions": len(sessions),
        "observed_sessions": len(observations),
        "missing_sessions": missing_sessions,
        "session_gap_fraction": gap_fraction,
        "median_daily_turnover_inr": median_turnover,
        "annualized_daily_return_volatility": return_volatility,
        "median_amihud_illiquidity": amihud_median,
        "quote_observations": quote_count,
        "valid_quote_observations": len(valid_quotes),
        "source_sha256": sorted({row.source_sha256 for row in observations} | quote_source_hashes),
        "stale_or_non_executable_quote_fraction": stale_fraction,
        "median_spread_bps": median_spread,
        "median_top_bid_depth_inr": median_bid_depth,
        "median_top_ask_depth_inr": median_ask_depth,
        "intended_notional_inr": intended_notional_inr,
        "capacity_participation_fraction": capacity_fraction,
        "amihud_size_impact_proxy_bps": impact_bps,
        "other_round_trip_cost_bps": other_round_trip_cost_bps,
        "estimated_all_in_round_trip_cost_bps": estimated_cost,
        "limits_met": limits_met,
        "cost_model_label": "Amihud size proxy plus observed half-spread plus caller-supplied other round-trip costs; not execution-calibrated",
    }


__all__ = ["ETFDailyLiquidityObservation", "analyze_etf_liquidity"]
