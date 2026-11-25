"""Cross-desk portfolio risk under supplied current and stressed correlation models.

All inputs are explicit, aligned, and caller-sourced. This module does not infer risk
factors or manufacture shocked returns: scenario matrices must be created by a separately
versioned historical-replay or stress-generation adapter.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

import numpy as np
import pandas as pd


class CrossDeskRiskError(ValueError):
    """Risk inputs are incomplete, inconsistent, or mathematically invalid."""


@dataclass(frozen=True)
class PortfolioRiskView:
    volatility: float
    marginal_contribution: Mapping[str, float]
    marginal_share: Mapping[str, float]
    expected_shortfall_loss: float
    tail_mean_return: float
    var_loss: float
    scenario_count: int


@dataclass(frozen=True)
class CrossDeskRiskReport:
    nav_inr: float
    gross_exposure_inr: float
    net_exposure_inr: float
    position_concentration_hhi: float
    factor_exposure_inr: Mapping[str, float]
    factor_concentration_hhi: float
    current: PortfolioRiskView
    shocked: PortfolioRiskView


def _finite_number(value: object, label: str) -> float:
    if isinstance(value, bool):
        raise CrossDeskRiskError(f"{label} must be finite numeric data")
    try:
        number = float(cast(Any, value))
    except (TypeError, ValueError) as exc:
        raise CrossDeskRiskError(f"{label} must be finite numeric data") from exc
    if not np.isfinite(number):
        raise CrossDeskRiskError(f"{label} must be finite numeric data")
    return number


def _covariance(cov: pd.DataFrame, names: list[str], label: str) -> np.ndarray:
    if not isinstance(cov, pd.DataFrame) or not cov.index.is_unique or not cov.columns.is_unique:
        raise CrossDeskRiskError(f"{label} covariance must have unique row and column labels")
    if set(cov.index) != set(names) or set(cov.columns) != set(names):
        raise CrossDeskRiskError(f"{label} covariance labels must exactly match portfolio positions")
    matrix = cov.loc[names, names].to_numpy(dtype=float)
    if not np.isfinite(matrix).all():
        raise CrossDeskRiskError(f"{label} covariance contains non-finite values")
    if not np.allclose(matrix, matrix.T, rtol=1e-9, atol=1e-12):
        raise CrossDeskRiskError(f"{label} covariance must be symmetric")
    if np.linalg.eigvalsh((matrix + matrix.T) / 2).min(initial=0.0) < -1e-9:
        raise CrossDeskRiskError(f"{label} covariance must be positive semidefinite")
    return matrix


def _risk_view(
    weights: np.ndarray,
    names: list[str],
    covariance: np.ndarray,
    scenarios: pd.DataFrame,
    confidence: float,
    label: str,
) -> PortfolioRiskView:
    if not isinstance(scenarios, pd.DataFrame) or set(scenarios.columns) != set(names):
        raise CrossDeskRiskError(f"{label} scenarios must contain exactly the portfolio positions")
    if len(scenarios) < 20:
        raise CrossDeskRiskError(f"{label} scenarios need at least 20 observations for tail risk")
    returns = scenarios.loc[:, names].to_numpy(dtype=float)
    if not np.isfinite(returns).all():
        raise CrossDeskRiskError(f"{label} scenarios contain non-finite returns")
    portfolio_returns = returns @ weights
    tail_n = max(1, int(np.ceil((1.0 - confidence) * len(portfolio_returns))))
    ordered = np.sort(portfolio_returns)
    tail_mean = float(ordered[:tail_n].mean())
    var_loss = float(max(0.0, -ordered[tail_n - 1]))
    es_loss = float(max(0.0, -tail_mean))

    sigma_w = covariance @ weights
    variance = float(weights @ sigma_w)
    if variance < -1e-9:
        raise CrossDeskRiskError(f"{label} portfolio variance is negative")
    volatility = float(np.sqrt(max(0.0, variance)))
    contributions = weights * sigma_w / volatility if volatility > 0 else np.zeros(len(names))
    shares = contributions / volatility if volatility > 0 else np.zeros(len(names))
    return PortfolioRiskView(
        volatility=volatility,
        marginal_contribution=dict(zip(names, map(float, contributions), strict=True)),
        marginal_share=dict(zip(names, map(float, shares), strict=True)),
        expected_shortfall_loss=es_loss,
        tail_mean_return=tail_mean,
        var_loss=var_loss,
        scenario_count=len(portfolio_returns),
    )


def analyze_cross_desk_risk(
    exposures_inr: Mapping[str, float],
    nav_inr: float,
    current_covariance: pd.DataFrame,
    shocked_covariance: pd.DataFrame,
    factor_loadings: Mapping[str, Mapping[str, float]],
    current_scenarios: pd.DataFrame,
    shocked_scenarios: pd.DataFrame,
    *,
    confidence: float = 0.95,
) -> CrossDeskRiskReport:
    """Build current/shocked risk, factor, concentration and historical-tail metrics.

    Covariance matrices must use the same horizon and asset units. Scenario rows must be
    point-in-time, source-audited portfolio-position returns for each respective state.
    Factor loadings are signed dimensionless exposures; an INR factor exposure is the
    sum of position INR notional times its factor loading. Short positions retain sign.
    """
    if not isinstance(exposures_inr, Mapping) or not exposures_inr:
        raise CrossDeskRiskError("exposures_inr must be a non-empty mapping")
    nav = _finite_number(nav_inr, "nav_inr")
    if nav <= 0:
        raise CrossDeskRiskError("nav_inr must be positive")
    conf = _finite_number(confidence, "confidence")
    if not 0 < conf < 1:
        raise CrossDeskRiskError("confidence must be between zero and one")
    if any(not isinstance(name, str) or not name.strip() for name in exposures_inr):
        raise CrossDeskRiskError("position identifiers must be non-empty strings")
    names = sorted(exposures_inr)
    exposure = np.array([_finite_number(exposures_inr[n], f"exposure {n}") for n in names])
    weights = exposure / nav
    current = _covariance(current_covariance, names, "current")
    shocked = _covariance(shocked_covariance, names, "shocked")
    if not isinstance(factor_loadings, Mapping) or set(factor_loadings) != set(names):
        raise CrossDeskRiskError("factor loadings must explicitly cover every portfolio position")
    factors: dict[str, float] = {}
    for index, name in enumerate(names):
        loading_row = factor_loadings[name]
        if not isinstance(loading_row, Mapping) or not loading_row:
            raise CrossDeskRiskError(f"factor loadings for {name} must be a non-empty mapping")
        for factor, raw_loading in loading_row.items():
            if not isinstance(factor, str) or not factor.strip():
                raise CrossDeskRiskError("factor names must be non-empty strings")
            loading = _finite_number(raw_loading, f"factor loading {factor}/{name}")
            factors[factor] = factors.get(factor, 0.0) + exposure[index] * loading
    gross = float(np.abs(exposure).sum())
    position_hhi = float(np.square(np.abs(exposure) / gross).sum()) if gross else 0.0
    factor_gross = float(sum(abs(value) for value in factors.values()))
    factor_hhi = (float(sum((abs(value) / factor_gross) ** 2 for value in factors.values()))
                  if factor_gross else 0.0)
    return CrossDeskRiskReport(
        nav_inr=nav,
        gross_exposure_inr=gross,
        net_exposure_inr=float(exposure.sum()),
        position_concentration_hhi=position_hhi,
        factor_exposure_inr=dict(sorted(factors.items())),
        factor_concentration_hhi=factor_hhi,
        current=_risk_view(weights, names, current, current_scenarios, conf, "current"),
        shocked=_risk_view(weights, names, shocked, shocked_scenarios, conf, "shocked"),
    )
