"""Equity and ETF research (FB-0314, FB-0327, FB-0354, FB-0357, FB-0358, FB-0363, FB-0364).

* ``browse_contracts`` filters futures contracts by symbol, expiry and currency.
* ``pearson`` is the correlation of two aligned series; ``correlation_matrix`` covers a set of series.
* ``split_adjusted_returns`` divides prices before a split by its ratio, so a split does not look like a loss.
* ``beta_and_residuals`` estimates beta against an index and the residual return each period.
* ``etf_fields`` reports tracking error and expense ratio. The expense ratio must be in [0, 1).
* ``drawdown_contribution`` returns the amount to add when drawdown breaches a threshold, else zero.
* ``usable_surprise`` returns the earnings surprise only when the consensus was known before the event.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from statistics import mean, pstdev


@dataclass(frozen=True)
class Contract:
    symbol: str
    expiry: str
    currency: str


def browse_contracts(
    contracts: Sequence[Contract], symbol: str | None = None, expiry: str | None = None, currency: str | None = None
) -> list[Contract]:
    return [
        c
        for c in contracts
        if (symbol is None or c.symbol == symbol)
        and (expiry is None or c.expiry == expiry)
        and (currency is None or c.currency == currency)
    ]


def pearson(x: Sequence[float], y: Sequence[float]) -> float:
    if len(x) != len(y) or len(x) < 2:
        raise ValueError("series must align and have at least two points")
    mx, my = mean(x), mean(y)
    sx = sum((a - mx) ** 2 for a in x) ** 0.5
    sy = sum((b - my) ** 2 for b in y) ** 0.5
    if sx == 0 or sy == 0:
        raise ValueError("a constant series has no correlation")
    return sum((a - mx) * (b - my) for a, b in zip(x, y, strict=True)) / (sx * sy)


def correlation_matrix(series: Mapping[str, Sequence[float]]) -> dict[tuple[str, str], float]:
    names = sorted(series)
    return {(a, b): pearson(series[a], series[b]) for a in names for b in names}


def split_adjusted_returns(prices: Sequence[float], splits: Mapping[int, float]) -> list[float]:
    adjusted = list(prices)
    for idx in sorted(splits, reverse=True):
        ratio = splits[idx]
        for i in range(idx):
            adjusted[i] = adjusted[i] / ratio
    return [adjusted[i] / adjusted[i - 1] - 1.0 for i in range(1, len(adjusted))]


def beta_and_residuals(stock: Sequence[float], index: Sequence[float]) -> tuple[float, list[float]]:
    if len(stock) != len(index) or len(stock) < 2:
        raise ValueError("series must align and have at least two points")
    ms, mi = mean(stock), mean(index)
    var_i = sum((i - mi) ** 2 for i in index)
    if var_i == 0:
        raise ValueError("index has no variation")
    beta = sum((s - ms) * (i - mi) for s, i in zip(stock, index, strict=True)) / var_i
    alpha = ms - beta * mi
    residuals = [s - (alpha + beta * i) for s, i in zip(stock, index, strict=True)]
    return beta, residuals


def etf_fields(portfolio: Sequence[float], benchmark: Sequence[float], expense_ratio: Decimal) -> dict[str, object]:
    if not (Decimal(0) <= expense_ratio < Decimal(1)):
        raise ValueError("expense ratio must be in [0, 1)")
    if len(portfolio) != len(benchmark) or len(portfolio) < 2:
        raise ValueError("series must align and have at least two points")
    te = pstdev([p - b for p, b in zip(portfolio, benchmark, strict=True)])
    return {"tracking_error": te, "expense_ratio": expense_ratio}


def drawdown_contribution(drawdown: Decimal, threshold: Decimal, contribution: Decimal) -> Decimal:
    if threshold <= 0:
        raise ValueError("threshold must be positive")
    return contribution if drawdown <= -threshold else Decimal(0)


def usable_surprise(actual: Decimal, consensus: Decimal, consensus_at: int, event_at: int) -> Decimal | None:
    if consensus_at >= event_at:
        return None
    return actual - consensus
