"""Frozen-schedule, low-turnover, risk-aware target weights for research/paper books (PE-0051)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import isfinite
from typing import Mapping

import numpy as np


class TargetPortfolioError(ValueError):
    pass


@dataclass(frozen=True)
class TargetPortfolio:
    as_of: date
    scheduled_rebalance: bool
    weights: tuple[tuple[str, float], ...]
    one_way_turnover: float
    cash_weight: float
    expected_net_alpha: float
    risk_penalty: float
    turnover_cost: float
    utility: float
    rebalance_triggered: bool = False
    no_trade_band: float = 0.005


def _project_capped_simplex(values: np.ndarray, *, budget: float, cap: float) -> np.ndarray:
    """Euclidean projection onto 0 <= w_i <= cap, sum(w) <= budget."""
    clipped = np.clip(values, 0.0, cap)
    if float(clipped.sum()) <= budget:
        return clipped
    lo, hi = float(np.min(values - cap)), float(np.max(values))
    for _ in range(80):
        threshold = (lo + hi) / 2
        projected = np.clip(values - threshold, 0.0, cap)
        if float(projected.sum()) > budget:
            lo = threshold
        else:
            hi = threshold
    return np.clip(values - hi, 0.0, cap)


def _cap_sector_weights(weights: np.ndarray, names: tuple[str, ...], sectors: Mapping[str, str],
                        max_sector_weight: float) -> np.ndarray:
    """Scale any over-cap sector into cash without reallocating into another sector."""
    out = weights.copy()
    for sector in sorted(set(sectors.values())):
        idx = [i for i, name in enumerate(names) if sectors[name] == sector]
        total = float(out[idx].sum())
        if total > max_sector_weight and total > 0:
            out[idx] *= max_sector_weight / total
    return out


def cash_days(weight_history: list[Mapping[str, float]], *, epsilon: float = 1e-12) -> int:
    """Count observations where explicit CASH has positive weight; this is not a session P&L metric."""
    if not 0 <= epsilon < 1:
        raise TargetPortfolioError("cash-day epsilon must be in [0, 1)")
    for weights in weight_history:
        if "CASH" not in weights or not isfinite(float(weights["CASH"])) or not 0 <= float(weights["CASH"]) <= 1:
            raise TargetPortfolioError("every target must contain finite CASH weight in [0, 1]")
        if abs(sum(float(v) for v in weights.values()) - 1.0) > 1e-8:
            raise TargetPortfolioError("target weights must sum to one")
    return sum(float(row["CASH"]) > epsilon for row in weight_history)


def plan_target_portfolio(*, as_of: date, scheduled_rebalances: frozenset[date],
                          expected_net_alpha: Mapping[str, float], covariance: np.ndarray,
                          covariance_names: tuple[str, ...], previous_weights: Mapping[str, float],
                          max_name_weight: float = 0.10, cash_floor: float = 0.0,
                          max_one_way_turnover: float = 0.20, turnover_cost_per_unit: float = 0.0,
                          no_trade_band: float = 0.005,
                          max_positions: int = 20, sectors: Mapping[str, str] | None = None,
                          max_sector_weight: float = 0.30,
                          risk_aversion: float = 1.0, iterations: int = 500) -> TargetPortfolio:
    """Optimize long-only target weights on a frozen rebalance schedule.

    `expected_net_alpha` is per-name expected return for one holding period after execution costs;
    covariance is for that same period. Utility is expected net alpha minus quadratic variance
    penalty and one-way turnover cost. Non-rebalance observations retain the previous book exactly.
    No forecasts or market outcomes are produced by this function.
    """
    names = tuple(sorted(expected_net_alpha))
    if not names or names != tuple(sorted(covariance_names)) or len(set(covariance_names)) != len(covariance_names):
        raise TargetPortfolioError("alpha names and unique covariance names must match and be non-empty")
    if not isinstance(covariance, np.ndarray) or covariance.shape != (len(names), len(names)):
        raise TargetPortfolioError("covariance shape must match the name universe")
    if not np.isfinite(covariance).all() or not np.allclose(covariance, covariance.T, atol=1e-10):
        raise TargetPortfolioError("covariance must be finite and symmetric")
    if float(np.linalg.eigvalsh(covariance).min()) < -1e-8:
        raise TargetPortfolioError("covariance must be positive semidefinite")
    if any(not isfinite(float(x)) for x in expected_net_alpha.values()):
        raise TargetPortfolioError("expected net alpha must be finite")
    if set(previous_weights) - set(names) - {"CASH"}:
        raise TargetPortfolioError("previous book contains names outside the frozen target universe")
    for value, label, upper in ((max_name_weight, "max_name_weight", 1.0), (cash_floor, "cash_floor", 1.0),
                                (max_one_way_turnover, "turnover cap", 1.0)):
        if not isfinite(value) or not 0 <= value <= upper:
            raise TargetPortfolioError(f"{label} must be in [0, 1]")
    if max_name_weight <= 0:
        raise TargetPortfolioError("max_name_weight must be positive")
    if not isfinite(turnover_cost_per_unit) or turnover_cost_per_unit < 0 or not isfinite(risk_aversion) \
            or risk_aversion < 0 or iterations < 1:
        raise TargetPortfolioError("risk, turnover cost and iteration settings must be non-negative")
    if not isfinite(no_trade_band) or not 0 <= no_trade_band <= 1:
        raise TargetPortfolioError("no-trade band must be in [0, 1]")
    if isinstance(max_positions, bool) or not isinstance(max_positions, int) or max_positions < 1:
        raise TargetPortfolioError("max_positions must be a positive integer")
    if not isfinite(max_sector_weight) or not 0 < max_sector_weight <= 1:
        raise TargetPortfolioError("max_sector_weight must be in (0, 1]")
    sector_map = dict(sectors) if sectors is not None else {name: name for name in names}
    if set(sector_map) != set(names) or any(not isinstance(value, str) or not value.strip()
                                            for value in sector_map.values()):
        raise TargetPortfolioError("sectors must name one non-empty sector for every target instrument")
    prev = np.array([float(previous_weights.get(name, 0.0)) for name in names], dtype=float)
    prev_cash = float(previous_weights.get("CASH", 0.0))
    if not np.isfinite(prev).all() or not isfinite(prev_cash) or np.any(prev < 0) or not 0 <= prev_cash <= 1 \
            or abs(float(prev.sum()) + prev_cash - 1.0) > 1e-8:
        raise TargetPortfolioError("previous weights must be finite, non-negative and sum to one with CASH")
    scheduled = as_of in scheduled_rebalances
    if scheduled and sum(weight > 1e-12 for weight in prev) > max_positions:
        raise TargetPortfolioError("previous book exceeds max_positions; resolve positions before rebalance")
    if scheduled and sectors is not None:
        for sector in set(sector_map.values()):
            prior_sector_weight = sum(prev[i] for i, name in enumerate(names) if sector_map[name] == sector)
            if prior_sector_weight > max_sector_weight + 1e-8:
                raise TargetPortfolioError("previous book exceeds max_sector_weight; resolve positions before rebalance")
    if scheduled and (np.any(prev > max_name_weight + 1e-8) or float(prev.sum()) > 1.0 - cash_floor + 1e-8):
        raise TargetPortfolioError("previous book violates name cap or cash floor; resolve outside turnover-constrained rebalance")
    if scheduled:
        mu = np.array([float(expected_net_alpha[name]) for name in names])
        selected = {name for i, name in enumerate(names) if prev[i] > 1e-12}
        selected.update(name for name in sorted(names, key=lambda name: (-float(expected_net_alpha[name]), name))
                        if name not in selected and len(selected) < max_positions)
        matrix = covariance[np.ix_([covariance_names.index(name) for name in names],
                                   [covariance_names.index(name) for name in names])]
        eig = max(float(np.linalg.eigvalsh(matrix).max()), 0.0)
        step_size = 1.0 / max(risk_aversion * eig, turnover_cost_per_unit, 1e-6)
        weights = prev.copy()
        budget = 1.0 - cash_floor
        for _ in range(iterations):
            z = weights + step_size * (mu - risk_aversion * (matrix @ weights))
            diff = z - prev
            shrink = np.sign(diff) * np.maximum(np.abs(diff) - step_size * turnover_cost_per_unit, 0.0)
            next_weights = _project_capped_simplex(prev + shrink, budget=budget, cap=max_name_weight)
            if float(np.abs(next_weights - weights).max()) < 1e-10:
                weights = next_weights
                break
            weights = next_weights
        weights[[name not in selected for name in names]] = 0.0
        within_band = (np.abs(weights - prev) <= no_trade_band) & np.array([name in selected for name in names])
        weights[within_band] = prev[within_band]
        if sectors is not None:
            weights = _cap_sector_weights(weights, names, sector_map, max_sector_weight)
        delta = float(np.abs(weights - prev).sum()) / 2.0
        if delta > max_one_way_turnover and delta > 0:
            weights = prev + (weights - prev) * (max_one_way_turnover / delta)
    else:
        weights = prev.copy()
    cash = max(0.0, 1.0 - float(weights.sum()))
    alpha = float(np.dot(np.array([float(expected_net_alpha[n]) for n in names]), weights)) if scheduled else 0.0
    variance = float(weights @ covariance[np.ix_([covariance_names.index(n) for n in names],
                                                  [covariance_names.index(n) for n in names])] @ weights)
    risk = risk_aversion * max(variance, 0.0) / 2.0 if scheduled else 0.0
    turnover = float(np.abs(weights - prev).sum()) / 2.0
    cost = turnover_cost_per_unit * turnover if scheduled else 0.0
    return TargetPortfolio(as_of, scheduled,
                           tuple((name, float(weights[i])) for i, name in enumerate(names)) + (("CASH", cash),),
                           turnover, cash, alpha, risk, cost, alpha - risk - cost,
                           scheduled and turnover > 1e-12, no_trade_band)
