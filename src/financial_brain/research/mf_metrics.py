"""Mutual-fund research metrics and decision vocabulary (checklist section 6).

NAV already carries the expense ratio, so returns are taken from NAV as published and TER is never
deducted again. Direct-versus-regular comparison uses each plan's own NAV series. Rolling metrics
need the full window; a short history returns None rather than an annualised guess. Decisions use
MF vocabulary (BUY_MORE, HOLD, REDUCE, SWITCH, REDEEM, NO_ACTION) with a category-specific minimum
holding period, so no fund is turned into a short-horizon trade.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum


class MfAction(str, Enum):
    BUY_MORE = "BUY_MORE"
    HOLD = "HOLD"
    REDUCE = "REDUCE"
    SWITCH = "SWITCH"
    REDEEM = "REDEEM"
    NO_ACTION = "NO_ACTION"


#: Minimum review horizon in days by broad category; owner-adjustable configuration, not advice.
MIN_HOLD_DAYS = {"EQUITY": 365, "HYBRID": 365, "DEBT": 90, "LIQUID": 1, "OVERNIGHT": 1, "COMMODITY": 180}


def nav_return(navs: list[float]) -> float:
    """Total return from published NAVs. TER is already inside NAV: nothing is deducted (FB-0317)."""
    if len(navs) < 2 or navs[0] <= 0:
        raise ValueError("need two positive NAVs")
    return navs[-1] / navs[0] - 1


@dataclass(frozen=True)
class Rolling:
    cagr: float
    volatility: float
    max_drawdown: float
    downside_deviation: float


def rolling_metrics(daily_navs: list[float], years: int, sessions_per_year: int = 250) -> Rolling | None:
    n = years * sessions_per_year
    if len(daily_navs) < n + 1:
        return None
    w = daily_navs[-(n + 1):]
    rets = [b / a - 1 for a, b in zip(w, w[1:], strict=False)]
    mean = sum(rets) / len(rets)
    vol = math.sqrt(sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)) * math.sqrt(sessions_per_year)
    down = [min(r, 0.0) for r in rets]
    dd_dev = math.sqrt(sum(d * d for d in down) / len(down)) * math.sqrt(sessions_per_year)
    peak, mdd = w[0], 0.0
    for v in w:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    return Rolling((w[-1] / w[0]) ** (1 / years) - 1, vol, mdd, dd_dev)


def direct_vs_regular(direct_navs: list[float], regular_navs: list[float]) -> float:
    """Return gap (direct minus regular) over the same dates, each from its own NAV series."""
    if len(direct_navs) != len(regular_navs):
        raise ValueError("series must cover the same dates")
    return nav_return(direct_navs) - nav_return(regular_navs)


def excess_vs_benchmark(fund_navs: list[float], benchmark_tri: list[float]) -> float:
    """Excess over the declared benchmark *total return* index on the same dates."""
    if len(fund_navs) != len(benchmark_tri):
        raise ValueError("series must cover the same dates")
    return nav_return(fund_navs) - nav_return(benchmark_tri)


def review(category: str, held_days: int, excess_1y: float | None, *,
           underperform: float, outperform: float) -> MfAction:
    """Category-aware action. Before the minimum horizon, or without a full year, nothing changes."""
    if category not in MIN_HOLD_DAYS:
        raise ValueError(f"unknown category {category}")
    if held_days < MIN_HOLD_DAYS[category] or excess_1y is None:
        return MfAction.NO_ACTION
    if excess_1y <= underperform:
        return MfAction.SWITCH
    if excess_1y >= outperform:
        return MfAction.BUY_MORE
    return MfAction.HOLD
