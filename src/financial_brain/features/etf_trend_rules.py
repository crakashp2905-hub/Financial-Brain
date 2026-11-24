"""Frozen ETF trend and medium-horizon mean-reversion candidate rules.

This module is a preregistered signal contract, not an evaluated strategy. It
does not place orders, choose parameters, or claim profitability. Calls over
historical observations are implementation checks only and are not a license
to inspect protected trial outcomes.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone

STRATEGY_ID = "ETF_TREND_MEAN_REVERSION"
STRATEGY_VERSION = "1.0.0"
RULES = (
    "TREND_50_200_CLOSE_ABOVE_SMA",
    "TREND_20_50_CLOSE_ABOVE_SMA",
    "MEAN_REVERSION_20D_ZSCORE",
)
VOLATILITY_LOOKBACK = 20
TARGET_ANNUAL_VOLATILITY = 0.10
MAX_GROSS_EXPOSURE = 1.0
MIN_GROSS_EXPOSURE = 0.0
MAX_MISSING_RETURN_SESSIONS = 0
SPEC = {
    "strategy_id": STRATEGY_ID,
    "version": STRATEGY_VERSION,
    "rules": RULES,
    "sma_periods_sessions": [20, 50, 200],
    "mean_reversion": {"lookback_sessions": 20, "entry_zscore": -2.0,
                        "exit_zscore": 0.0, "long_only": True},
    "volatility_sizing": {"return_lookback_sessions": VOLATILITY_LOOKBACK,
                           "target_annualized_volatility": TARGET_ANNUAL_VOLATILITY,
                           "min_gross_exposure": MIN_GROSS_EXPOSURE,
                           "max_gross_exposure": MAX_GROSS_EXPOSURE,
                           "annualization_sessions": 252},
    "input_policy": "exact_sourced_sessions_no_fill_no_bridge_timezone_aware_availability_at_or_before_as_of",
    "signal_timing": "close_only_for_next_session_review",
    "execution": "none_candidate_only",
}
SPEC_SHA256 = hashlib.sha256(json.dumps(SPEC, sort_keys=True, separators=(",", ":"),
                                      allow_nan=False).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ETFTrendCandidate:
    isin: str
    symbol: str
    as_of: date
    rule_signals: tuple[tuple[str, bool], ...]
    twenty_session_zscore: float
    annualized_volatility: float
    volatility_target_gross_exposure: float
    strategy_id: str = STRATEGY_ID
    strategy_version: str = STRATEGY_VERSION
    strategy_spec_sha256: str = SPEC_SHA256
    input_sha256: str = ""
    candidate_only: bool = True
    orders_authorized: bool = False


def build_etf_trend_candidates(
    *,
    sessions: Sequence[date],
    closes_by_isin: Mapping[str, Mapping[date, float]],
    source_hashes_by_isin: Mapping[str, Mapping[date, str]],
    available_at_by_isin: Mapping[str, Mapping[date, datetime]],
    symbols_by_isin: Mapping[str, str],
    as_of: datetime,
) -> list[ETFTrendCandidate]:
    """Compute frozen candidate flags from exact sourced close series.

    A signal at the latest close is reviewable for a later session; it is not
    an executable instruction. Rows require 200 closes for the slow average,
    a nonzero 20-session sample standard deviation, and 20 adjacent returns.
    """
    days = tuple(sessions)
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone aware")
    if not days or any(type(day) is not date for day in days):
        raise ValueError("sessions must be non-empty calendar dates")
    if tuple(sorted(set(days))) != days:
        raise ValueError("sessions must be unique and strictly ordered")
    end = days[-1]
    cutoff = as_of.astimezone(timezone.utc)
    if end > cutoff.date():
        raise ValueError("latest session cannot follow the as_of timestamp")
    output: list[ETFTrendCandidate] = []
    for isin in sorted(closes_by_isin):
        closes = closes_by_isin[isin]
        hashes = source_hashes_by_isin.get(isin, {})
        availability = available_at_by_isin.get(isin, {})
        if any(day not in days for day in closes):
            continue
        if len(days) < 201 or any(day not in closes or day not in hashes or day not in availability
                                  for day in days[-201:]):
            continue
        observed_times = [availability[day] for day in days[-201:]]
        if any(value.tzinfo is None or value.utcoffset() is None for value in observed_times):
            raise ValueError(f"{isin} availability timestamps must be timezone aware")
        if any(value.astimezone(timezone.utc) > cutoff for value in observed_times):
            continue
        values = [closes[day] for day in days[-201:]]
        if any(not math.isfinite(value) or value <= 0 for value in values):
            raise ValueError(f"{isin} closes must be finite and positive")
        used_hashes = [hashes[day] for day in days[-201:]]
        if any(not isinstance(value, str) or len(value) != 64
               or any(char not in "0123456789abcdef" for char in value)
               for value in used_hashes):
            raise ValueError(f"{isin} requires lowercase SHA-256 for each selected close")

        close = values[-1]
        sma20 = sum(values[-20:]) / 20
        sma50 = sum(values[-50:]) / 50
        sma200 = sum(values[-200:]) / 200
        mean20 = sma20
        variance20 = sum((value - mean20) ** 2 for value in values[-20:]) / 19
        sd20 = math.sqrt(variance20)
        if sd20 == 0:
            continue
        zscore = (close - mean20) / sd20
        daily_returns = [values[i] / values[i - 1] - 1 for i in range(-20, 0)]
        mean_return = sum(daily_returns) / len(daily_returns)
        daily_variance = sum((value - mean_return) ** 2 for value in daily_returns) / (len(daily_returns) - 1)
        annualized_volatility = math.sqrt(daily_variance * 252)
        if annualized_volatility <= 0 or not math.isfinite(annualized_volatility):
            continue
        exposure = min(MAX_GROSS_EXPOSURE,
                       max(MIN_GROSS_EXPOSURE, TARGET_ANNUAL_VOLATILITY / annualized_volatility))
        input_hash = hashlib.sha256(json.dumps(
            {"sessions": [day.isoformat() for day in days[-201:]], "source_hashes": used_hashes,
             "available_at": [value.astimezone(timezone.utc).isoformat() for value in observed_times],
             "as_of": cutoff.isoformat(),
             "strategy_spec_sha256": SPEC_SHA256}, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")).hexdigest()
        output.append(ETFTrendCandidate(
            isin=isin, symbol=symbols_by_isin.get(isin, isin), as_of=end,
            rule_signals=(
                ("TREND_50_200_CLOSE_ABOVE_SMA", close > sma50 and sma50 > sma200),
                ("TREND_20_50_CLOSE_ABOVE_SMA", close > sma20 and sma20 > sma50),
                ("MEAN_REVERSION_20D_ZSCORE", zscore <= -2.0),
            ),
            twenty_session_zscore=zscore,
            annualized_volatility=annualized_volatility,
            volatility_target_gross_exposure=exposure,
            input_sha256=input_hash,
        ))
    return output


__all__ = ["ETFTrendCandidate", "RULES", "SPEC", "SPEC_SHA256", "STRATEGY_ID",
           "STRATEGY_VERSION", "build_etf_trend_candidates"]
