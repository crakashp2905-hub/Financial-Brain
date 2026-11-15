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


def _project_capped_simplex(values: np.ndarray, *, budget: float, cap: float,
                            name_caps: np.ndarray | None = None) -> np.ndarray:
    """Euclidean projection onto 0 <= w_i <= cap, sum(w) <= budget."""
    caps = np.full_like(values, cap) if name_caps is None else name_caps
    clipped = np.clip(values, 0.0, caps)
    if float(clipped.sum()) <= budget:
        return clipped
    lo, hi = float(np.min(values - caps)), float(np.max(values))
    for _ in range(80):
        threshold = (lo + hi) / 2
        projected = np.clip(values - threshold, 0.0, caps)
        if float(projected.sum()) > budget:
            lo = threshold
        else:
            hi = threshold
    return np.clip(values - hi, 0.0, caps)


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
                          no_trade_band: float = 0.0,
                          max_positions: int | None = None,
                          sector_by_name: Mapping[str, str] | None = None,
                          max_sector_weight: float | None = None,
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
    if not isfinite(no_trade_band) or not 0 <= no_trade_band <= 1:
        raise TargetPortfolioError("no-trade band must be in [0, 1]")
    if max_positions is not None and (isinstance(max_positions, bool) or max_positions < 1):
        raise TargetPortfolioError("max_positions must be a positive integer")
    if max_sector_weight is not None and (not isfinite(max_sector_weight) or not 0 <= max_sector_weight <= 1):
        raise TargetPortfolioError("max_sector_weight must be in [0, 1]")
    if max_sector_weight is not None and (sector_by_name is None or set(sector_by_name) != set(names) or
                                          any(not value.strip() for value in sector_by_name.values())):
        raise TargetPortfolioError("sector caps require one non-empty sector per target name")
    if not isfinite(turnover_cost_per_unit) or turnover_cost_per_unit < 0 or not isfinite(risk_aversion) \
            or risk_aversion < 0 or iterations < 1:
        raise TargetPortfolioError("risk, turnover cost and iteration settings must be non-negative")
    prev = np.array([float(previous_weights.get(name, 0.0)) for name in names], dtype=float)
    prev_cash = float(previous_weights.get("CASH", 0.0))
    if not np.isfinite(prev).all() or not isfinite(prev_cash) or np.any(prev < 0) or not 0 <= prev_cash <= 1 \
            or abs(float(prev.sum()) + prev_cash - 1.0) > 1e-8:
        raise TargetPortfolioError("previous weights must be finite, non-negative and sum to one with CASH")
    if max_positions is not None and int(np.count_nonzero(prev > 1e-12)) > max_positions:
        raise TargetPortfolioError("previous book exceeds max_positions; reduce holdings before optimization")
    if max_sector_weight is not None:
        previous_sector_totals: dict[str, float] = {}
        for index, name in enumerate(names):
            sector = sector_by_name[name]  # type: ignore[index]
            previous_sector_totals[sector] = previous_sector_totals.get(sector, 0.0) + float(prev[index])
        if any(weight > max_sector_weight + 1e-8 for weight in previous_sector_totals.values()):
            raise TargetPortfolioError("previous book exceeds max_sector_weight; reduce holdings before optimization")
    scheduled = as_of in scheduled_rebalances
    if scheduled and (np.any(prev > max_name_weight + 1e-8) or float(prev.sum()) > 1.0 - cash_floor + 1e-8):
        raise TargetPortfolioError("previous book violates name cap or cash floor; resolve outside turnover-constrained rebalance")
    if scheduled:
        mu = np.array([float(expected_net_alpha[name]) for name in names])
        matrix = covariance[np.ix_([covariance_names.index(name) for name in names],
                                   [covariance_names.index(name) for name in names])]
        eig = max(float(np.linalg.eigvalsh(matrix).max()), 0.0)
        name_caps = np.full(len(names), max_name_weight, dtype=float)
        if max_positions is not None and max_positions < len(names):
            ranked = sorted(range(len(names)), key=lambda index: (-mu[index], names[index]))
            name_caps[[index for index in ranked[max_positions:]]] = 0.0
        step_size = 1.0 / max(risk_aversion * eig, turnover_cost_per_unit, 1e-6)
        weights = prev.copy()
        budget = 1.0 - cash_floor
        for _ in range(iterations):
            z = weights + step_size * (mu - risk_aversion * (matrix @ weights))
            diff = z - prev
            shrink = np.sign(diff) * np.maximum(np.abs(diff) - step_size * turnover_cost_per_unit, 0.0)
            next_weights = _project_capped_simplex(prev + shrink, budget=budget, cap=max_name_weight,
                                                   name_caps=name_caps)
            if float(np.abs(next_weights - weights).max()) < 1e-10:
                weights = next_weights
                break
            weights = next_weights
        delta = float(np.abs(weights - prev).sum()) / 2.0
        if delta > max_one_way_turnover and delta > 0:
            weights = prev + (weights - prev) * (max_one_way_turnover / delta)
        if float(np.abs(weights - prev).sum()) / 2.0 <= no_trade_band:
            weights = prev.copy()
        if max_sector_weight is not None:
            sector_totals: dict[str, float] = {}
            for index, name in enumerate(names):
                sector = sector_by_name[name]  # type: ignore[index]
                sector_totals[sector] = sector_totals.get(sector, 0.0) + float(weights[index])
            for sector, total in sector_totals.items():
                if total > max_sector_weight and total > 0:
                    scale = max_sector_weight / total
                    for index, name in enumerate(names):
                        if sector_by_name[name] == sector:  # type: ignore[index]
                            weights[index] *= scale
        if max_positions is not None and int(np.count_nonzero(weights > 1e-12)) > max_positions:
            raise TargetPortfolioError("turnover cap prevents reaching max_positions; no target returned")
        final_turnover = float(np.abs(weights - prev).sum()) / 2.0
        if final_turnover > max_one_way_turnover + 1e-8:
            raise TargetPortfolioError("sector and position caps require turnover above the configured limit")
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
                           turnover, cash, alpha, risk, cost, alpha - risk - cost)
