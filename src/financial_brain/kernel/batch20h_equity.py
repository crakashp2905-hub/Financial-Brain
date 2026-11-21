"""Equity and portfolio checks (FB-0322, FB-0355, FB-0356, FB-0359, FB-0362, FB-0365, FB-0367, FB-0372, FB-0381).

* ``margin_balances`` classifies equity against initial and maintenance requirements.
* ``breadth`` gives net advances minus declines and the advancing share.
* ``relative_strength`` compares a sector return with an index return.
* ``annualised_turnover`` scales traded value by average equity to a yearly rate.
* ``valuation_scenarios`` gives value per share as earnings times each scenario multiple.
* ``factor_decay_exit`` signals exit when a factor score has stayed below a threshold for consecutive periods.
* ``contribution`` gives each asset's weight times its return; the sum is the portfolio return.
* ``gap_and_go`` is True when the open gaps above the prior high and the bar closes higher than it opened.
* ``phase_returns`` gives the morning and afternoon returns from open, midday and close prices.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal


def margin_balances(equity: Decimal, initial_req: Decimal, maintenance_req: Decimal) -> str:
    if maintenance_req > initial_req:
        raise ValueError("maintenance requirement cannot exceed initial requirement")
    if equity < maintenance_req:
        return "MARGIN_CALL"
    if equity < initial_req:
        return "BELOW_INITIAL"
    return "OK"


def breadth(advances: int, declines: int) -> dict[str, Decimal | None]:
    if advances < 0 or declines < 0:
        raise ValueError("counts must be non-negative")
    total = advances + declines
    ratio = Decimal(advances) / Decimal(total) if total else None
    return {"net": Decimal(advances - declines), "advancing_share": ratio}


def relative_strength(sector_return: Decimal, index_return: Decimal) -> Decimal:
    if index_return <= -1:
        raise ValueError("index return must exceed -100%")
    return (1 + sector_return) / (1 + index_return) - 1


def annualised_turnover(
    trade_values: Sequence[Decimal], avg_equity: Decimal, periods_per_year: int, n_periods: int
) -> Decimal:
    if avg_equity <= 0 or n_periods <= 0:
        raise ValueError("equity and period count must be positive")
    traded = sum((abs(t) for t in trade_values), Decimal(0))
    return traded / avg_equity * Decimal(periods_per_year) / Decimal(n_periods)


def valuation_scenarios(eps: Decimal, multiples: Mapping[str, Decimal]) -> dict[str, Decimal]:
    return {name: eps * m for name, m in multiples.items()}


def factor_decay_exit(scores: Sequence[float], threshold: float, consecutive: int) -> bool:
    if consecutive < 1:
        raise ValueError("consecutive must be positive")
    if len(scores) < consecutive:
        return False
    return all(s < threshold for s in scores[-consecutive:])


def contribution(weights: Mapping[str, Decimal], returns: Mapping[str, Decimal]) -> dict[str, Decimal]:
    if set(weights) != set(returns):
        raise ValueError("weights and returns must cover the same assets")
    return {a: weights[a] * returns[a] for a in weights}


def gap_and_go(prev_high: Decimal, open_: Decimal, close: Decimal) -> bool:
    return open_ > prev_high and close > open_


def phase_returns(open_p: Decimal, midday_p: Decimal, close_p: Decimal) -> dict[str, Decimal]:
    if open_p <= 0 or midday_p <= 0:
        raise ValueError("prices must be positive")
    return {"morning": midday_p / open_p - 1, "afternoon": close_p / midday_p - 1}
