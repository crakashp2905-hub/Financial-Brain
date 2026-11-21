"""Batch of 25 partial-item closures.

Items covered: FB-0118, FB-0141, FB-0149, FB-0154, FB-0226, FB-0230, FB-0243, FB-0262, FB-0280, FB-0286, FB-0318,
FB-0328, FB-0332, FB-0345, FB-0352, FB-0354, FB-0371, FB-0384, FB-0420, FB-0421, FB-0439, FB-0448, FB-0449, FB-0452,
FB-0465.

Each function is a small, deterministic piece of its item's acceptance. The ledger note records what is still missing.
"""
from __future__ import annotations

import csv
import io
import math
import tracemalloc
from functools import partial
from collections.abc import Callable, Mapping, Sequence
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, TypeVar

from financial_brain.kernel.batch20c_strategies import param_distribution
from financial_brain.kernel.batch20e_strategies import covered_call_profit
from financial_brain.kernel.batch20i_ops import top_k_watchlist
from financial_brain.kernel.batch20g_markets import protective_put_profit
from financial_brain.kernel.execution_tools import slippage_bps
from financial_brain.kernel.portfolio_risk import max_drawdown
from financial_brain.kernel.research_assets import result_diff
from financial_brain.kernel.research_extra import bootstrap_ci
from financial_brain.kernel.research_pipeline import run_bounded

T = TypeVar("T")


# FB-0118: detector state distinguishes warm-up, uncertain and confirmed.
def detector_state(value: float | None, warmup: int, observed: int) -> str:
    if observed < warmup:
        return "WARMUP"
    return "CONFIRMED" if value is not None else "UNCERTAIN"


# FB-0141: difference between two versions of a rule or strategy spec.
def diff_versions(old: Mapping[str, Any], new: Mapping[str, Any]) -> dict[str, list[str]]:
    return result_diff(old, new)


# FB-0149: cluster-robust standard error of the mean.
def clustered_mean_se(values: Sequence[float], clusters: Sequence[str]) -> float:
    if len(values) != len(clusters) or len(values) < 2:
        raise ValueError("values and clusters must align, with at least two points")
    n = len(values)
    mean = sum(values) / n
    sums: dict[str, float] = {}
    for v, c in zip(values, clusters, strict=True):
        sums[c] = sums.get(c, 0.0) + (v - mean)
    return math.sqrt(sum(s * s for s in sums.values())) / n


# FB-0154: bootstrap interval for maximum drawdown of a return path.
def drawdown_interval(returns: Sequence[float], n_boot: int = 500, seed: int = 0) -> tuple[float, float]:
    def dd(sample: Sequence[float]) -> float:
        equity = [1.0]
        for r in sample:
            equity.append(equity[-1] * (1.0 + r))
        return max_drawdown(equity)

    return bootstrap_ci(list(returns), stat=dd, n_boot=n_boot, seed=seed)


# FB-0226: fill quality against arrival, midpoint and VWAP benchmarks (basis points, positive is adverse).
def fill_quality(fill: Decimal, arrival: Decimal, mid: Decimal, vwap: Decimal, side: str) -> dict[str, Decimal]:
    return {
        "vs_arrival_bps": slippage_bps(fill, arrival, side),
        "vs_mid_bps": slippage_bps(fill, mid, side),
        "vs_vwap_bps": slippage_bps(fill, vwap, side),
    }


# FB-0230: translate an amount at a dated rate; a missing date is refused.
def translate_at_date(amount: Decimal, day: str, rates: Mapping[str, Decimal]) -> Decimal:
    if day not in rates:
        raise KeyError(f"no FX rate for {day}")
    return amount * rates[day]


# FB-0243: tax and fee decomposition of a gross return, summing back to the total.
def decompose_cost(gross: Decimal, tax: Decimal, fee: Decimal) -> dict[str, Decimal]:
    if tax < 0 or fee < 0:
        raise ValueError("tax and fee must be non-negative")
    net = gross - tax - fee
    return {"gross": gross, "tax": tax, "fee": fee, "net": net, "total_costs": tax + fee}


# FB-0262: tax drag is the gap between pre-tax and post-tax return.
def tax_drag(pre_tax_return: float, post_tax_return: float) -> float:
    return pre_tax_return - post_tax_return


# FB-0280: clock-drift breaker trips when local and exchange clocks diverge too far.
def clock_drift_breaker(local_ms: int, exchange_ms: int, max_drift_ms: int) -> str:
    if max_drift_ms < 0:
        raise ValueError("max drift must be non-negative")
    return "TRIP" if abs(local_ms - exchange_ms) > max_drift_ms else "OK"


# FB-0286: debit-vertical max loss including fees.
def max_loss_with_fees(debit: Decimal, fees: Decimal) -> Decimal:
    if debit <= 0 or fees < 0:
        raise ValueError("debit must be positive and fees non-negative")
    return debit + fees


# FB-0318: roll on a volume criterion.
def roll_by_volume(front_volume: float, back_volume: float) -> str:
    return "ROLL" if back_volume > front_volume else "HOLD"


# FB-0328: roll date as a business-day offset before expiry, skipping weekends and holidays.
def roll_date_offset(expiry: date, offset_bdays: int, holidays: frozenset[date]) -> date:
    if offset_bdays < 0:
        raise ValueError("offset must be non-negative")
    day = expiry
    remaining = offset_bdays
    while remaining > 0:
        day -= timedelta(days=1)
        if day.weekday() < 5 and day not in holidays:
            remaining -= 1
    return day


# FB-0332: reconciliation differences as a CSV export.
def reconcile_export(diffs: Mapping[str, int]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["symbol", "difference"])
    for symbol in sorted(diffs):
        writer.writerow([symbol, diffs[symbol]])
    return buf.getvalue().rstrip("\n")


# FB-0345: pip journal totals.
def journal_pips(pips: Sequence[float]) -> dict[str, float]:
    return {"total": sum(pips), "trades": float(len(pips))}


# FB-0352: value, quality and growth factors from fundamentals (momentum and low-risk already exist).
def fundamental_factors(earnings_yield: float, roe: float, eps_growth: float) -> dict[str, float]:
    return {"value": earnings_yield, "quality": roe, "growth": eps_growth}


# FB-0354: dividend adjustment of a price series. The price before each ex-date is scaled by (1 - dividend/price).
def adjust_for_dividends(prices: Sequence[float], dividends: Mapping[int, float]) -> list[float]:
    adjusted = list(prices)
    for idx in sorted(dividends, reverse=True):
        if not 0 < idx < len(prices):
            raise ValueError("ex-date index out of range")
        factor = 1.0 - dividends[idx] / prices[idx]
        for i in range(idx):
            adjusted[i] *= factor
    return adjusted


# FB-0371: rolling participation as a rolling mean of volume.
def rolling_participation(volumes: Sequence[float], window: int) -> list[float | None]:
    if window < 1:
        raise ValueError("window must be positive")
    out: list[float | None] = []
    for i in range(len(volumes)):
        if i + 1 < window:
            out.append(None)
        else:
            out.append(sum(volumes[i + 1 - window : i + 1]) / window)
    return out


# FB-0384: trailing stop level and time exit.
def trailing_stop_level(peak: float, trail_pct: float) -> float:
    if not 0 < trail_pct < 1:
        raise ValueError("trail must be between 0 and 1")
    return peak * (1 - trail_pct)


def time_exit(bars_held: int, max_bars: int) -> bool:
    return bars_held >= max_bars


# FB-0420: peak memory of a call, via tracemalloc.
def profile_memory(fn: Callable[[], T]) -> tuple[T, int]:
    tracemalloc.start()
    try:
        result = fn()
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return result, peak


# FB-0421: experiment runner over a parameter list with bounded concurrency; results keep input order.
def experiment_run(params: Sequence[Mapping[str, Any]], fn: Callable[[Mapping[str, Any]], T], max_workers: int) -> list[T]:
    tasks: list[Callable[[], T]] = [partial(fn, p) for p in params]
    return run_bounded(tasks, max_workers)


# FB-0439: factor baselines selecting the top fraction by low volatility, quality (ROE) and value (low P/E).
def factor_baseline(
    stocks: Mapping[str, tuple[float, float, float]], fraction: float
) -> dict[str, list[str]]:
    if not (0 < fraction <= 1):
        raise ValueError("fraction must be in (0, 1]")
    k = max(1, round(len(stocks) * fraction))
    low_vol = sorted(stocks, key=lambda s: (stocks[s][0], s))[:k]
    quality = top_k_watchlist({s: v[1] for s, v in stocks.items()}, k)
    value = sorted(stocks, key=lambda s: (stocks[s][2], s))[:k]
    return {"low_vol": low_vol, "quality": quality, "value": value}


# FB-0448: covered call and protective put demonstration with fixed inputs.
def covered_and_protective_demo() -> dict[str, float]:
    return {
        "covered_call_at_120": covered_call_profit(100.0, 110.0, 2.0, expiry_spot=120.0, shares=10),
        "protective_put_at_90": protective_put_profit(100.0, 95.0, 2.0, expiry_spot=90.0, shares=10),
    }


# FB-0449: credit vertical limits: max profit is the credit, max loss is width minus credit.
def credit_vertical_limits(short_strike: float, long_strike: float, credit: float) -> dict[str, float]:
    width = abs(short_strike - long_strike)
    if not 0 < credit < width:
        raise ValueError("credit must be positive and below the strike width")
    return {"max_profit": credit, "max_loss": width - credit}


# FB-0452: dividend reinvestment followed by the resulting unit count (rebalance is then a weight change).
def dividend_reinvestment(units: int, price: float, dividend_per_unit: float) -> int:
    if price <= 0:
        raise ValueError("price must be positive")
    return units + math.floor(units * dividend_per_unit / price)


# FB-0465: parameter frequencies and mode across walk-forward folds (the report).
def walk_forward_parameter_report(chosen: Sequence[str]) -> dict[str, Any]:
    counts, mode = param_distribution(list(chosen))
    return {"counts": counts, "mode": mode}
