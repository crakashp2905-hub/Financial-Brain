"""Volatility and option analytics (FB-0297, FB-0301, FB-0305, FB-0308, FB-0309, FB-0311).

* ``skew`` is put IV minus call IV at the same delta.
* ``term_structure_slope`` is the IV change per unit of tenor between the shortest and longest tenor.
* ``realised_vol`` annualises the standard deviation of periodic returns; ``iv_rv_spread`` is IV minus realised vol.
* ``greeks_heatmap`` evaluates a supplied pricer over a grid of spot values and volatility shifts.
* ``theta_gamma_ratio`` is an educational trade-off measure. It is labelled EDUCATIONAL, never a trade signal.
* ``index_vs_stock_rules`` gives the exercise style and settlement for index and stock options.
* ``protective_put_profit`` is the profit at expiry of a long stock with a protective put, for a given number of shares.
"""
from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from statistics import pstdev


def skew(iv_put: float, iv_call: float) -> float:
    return iv_put - iv_call


def term_structure_slope(points: dict[int, float]) -> float:
    if len(points) < 2:
        raise ValueError("need at least two tenors")
    tenors = sorted(points)
    t0, t1 = tenors[0], tenors[-1]
    return (points[t1] - points[t0]) / (t1 - t0)


def realised_vol(returns: Sequence[float], periods_per_year: int = 252) -> float:
    if len(returns) < 2:
        raise ValueError("need at least two returns")
    return pstdev(returns) * math.sqrt(periods_per_year)


def iv_rv_spread(iv: float, rv: float) -> float:
    return iv - rv


def greeks_heatmap(
    pricer: Callable[[float, float], float], spots: Sequence[float], vol_shifts: Sequence[float]
) -> list[list[float]]:
    return [[pricer(s, v) for v in vol_shifts] for s in spots]


def theta_gamma_ratio(theta: float, gamma: float) -> dict[str, float | str]:
    if gamma == 0:
        raise ValueError("gamma is zero; ratio undefined")
    return {"ratio": -theta / gamma, "label": "EDUCATIONAL"}


def index_vs_stock_rules(kind: str) -> dict[str, str]:
    if kind == "INDEX":
        return {"exercise": "EUROPEAN", "settlement": "CASH"}
    if kind == "STOCK":
        return {"exercise": "AMERICAN", "settlement": "PHYSICAL"}
    raise ValueError("kind must be INDEX or STOCK")


def protective_put_profit(spot0: float, put_strike: float, premium: float, expiry_spot: float, shares: int) -> float:
    per_share = (expiry_spot - spot0) + max(put_strike - expiry_spot, 0.0) - premium
    return per_share * shares
