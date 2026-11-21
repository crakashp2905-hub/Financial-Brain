"""Bounded volatility-targeted, CVaR-penalized and concentration-aware allocation (FE-0106)."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Mapping

import numpy as np


class RiskAllocatorError(ValueError):
    """Invalid as-of risk input or allocation policy."""


@dataclass(frozen=True)
class CandidateRisk:
    candidate_id: str
    instrument_id: str
    sector: str
    expected_net_alpha: float
    volatility: float
    cvar_loss: float

    def __post_init__(self) -> None:
        if not all(value.strip() for value in (self.candidate_id, self.instrument_id, self.sector)):
            raise RiskAllocatorError("candidate identity, instrument and sector are required")
        if not all(math.isfinite(value) for value in (self.expected_net_alpha, self.volatility, self.cvar_loss)):
            raise RiskAllocatorError("risk inputs must be finite")
        if self.volatility <= 0 or self.cvar_loss < 0:
            raise RiskAllocatorError("volatility must be positive and CVaR loss non-negative")


@dataclass(frozen=True)
class RiskAllocationPolicy:
    max_invested_fraction: float
    max_single_name_fraction: float
    sector_caps: Mapping[str, float]
    target_portfolio_volatility: float
    volatility_penalty: float
    cvar_penalty: float
    concentration_penalty: float
    step_size: float = 0.01
    iterations: int = 500

    def __post_init__(self) -> None:
        unit_values = (self.max_invested_fraction, self.max_single_name_fraction,
                       *self.sector_caps.values())
        if any(not math.isfinite(value) or not 0 < value <= 1 for value in unit_values):
            raise RiskAllocatorError("investment, name and sector fractions must be in (0, 1]")
        if not self.sector_caps or any(not key.strip() for key in self.sector_caps):
            raise RiskAllocatorError("sector caps must be explicit")
        if not math.isfinite(self.target_portfolio_volatility) or self.target_portfolio_volatility <= 0:
            raise RiskAllocatorError("target volatility must be finite and positive")
        if any(not math.isfinite(value) or value < 0 for value in
               (self.volatility_penalty, self.cvar_penalty, self.concentration_penalty)):
            raise RiskAllocatorError("risk penalties must be finite and non-negative")
        if not math.isfinite(self.step_size) or self.step_size <= 0 or type(self.iterations) is not int or self.iterations < 1:
            raise RiskAllocatorError("step size and iteration count must be positive")


@dataclass(frozen=True)
class RiskAllocation:
    weights: Mapping[str, float]
    cash_weight: float
    forecast_portfolio_volatility: float
    weighted_cvar_loss: float
    objective_value: float
    promotion_allowed: bool
    report_hash: str


def allocate_with_risk_controls(candidates: tuple[CandidateRisk, ...],
                                correlation_matrix: tuple[tuple[float, ...], ...],
                                policy: RiskAllocationPolicy) -> RiskAllocation:
    """Optimize a deterministic concave score from frozen as-of inputs, then enforce hard caps."""
    if not candidates or len({candidate.candidate_id for candidate in candidates}) != len(candidates):
        raise RiskAllocatorError("candidate set must be explicit and IDs unique")
    ordered = tuple(sorted(candidates, key=lambda item: item.candidate_id))
    if any(candidate.sector not in policy.sector_caps for candidate in ordered):
        raise RiskAllocatorError("every candidate sector needs a frozen cap")
    matrix = np.asarray(correlation_matrix, dtype=float)
    n = len(ordered)
    if matrix.shape != (n, n) or not np.isfinite(matrix).all():
        raise RiskAllocatorError("correlation matrix dimensions must match sorted candidate IDs")
    if not np.allclose(matrix, matrix.T, atol=1e-10) or not np.allclose(np.diag(matrix), 1.0, atol=1e-10):
        raise RiskAllocatorError("correlation matrix must be symmetric with a unit diagonal")
    if np.any(np.abs(matrix) > 1.0 + 1e-10) or float(np.linalg.eigvalsh(matrix).min()) < -1e-8:
        raise RiskAllocatorError("correlation matrix must be positive semidefinite and bounded")
    vols = np.asarray([candidate.volatility for candidate in ordered])
    cov = matrix * np.outer(vols, vols)
    alpha = np.asarray([candidate.expected_net_alpha for candidate in ordered])
    cvar = np.asarray([candidate.cvar_loss for candidate in ordered])
    weights = np.zeros(n, dtype=float)
    name_groups: dict[str, list[int]] = {}
    sector_groups: dict[str, list[int]] = {}
    for index, candidate in enumerate(ordered):
        name_groups.setdefault(candidate.instrument_id, []).append(index)
        sector_groups.setdefault(candidate.sector, []).append(index)

    def project(values: np.ndarray) -> np.ndarray:
        projected = np.maximum(values, 0.0)
        for indices in name_groups.values():
            total = float(projected[indices].sum())
            if total > policy.max_single_name_fraction:
                projected[indices] *= policy.max_single_name_fraction / total
        for sector, indices in sector_groups.items():
            total = float(projected[indices].sum())
            cap = policy.sector_caps[sector]
            if total > cap:
                projected[indices] *= cap / total
        total = float(projected.sum())
        if total > policy.max_invested_fraction:
            projected *= policy.max_invested_fraction / total
        portfolio_vol = math.sqrt(max(0.0, float(projected @ cov @ projected)))
        if portfolio_vol > policy.target_portfolio_volatility:
            projected *= policy.target_portfolio_volatility / portfolio_vol
        return projected

    for _ in range(policy.iterations):
        sector_totals = {sector: float(weights[indices].sum()) for sector, indices in sector_groups.items()}
        marginal = (alpha - policy.cvar_penalty * cvar
                    - 2.0 * policy.volatility_penalty * (cov @ weights))
        for index, candidate in enumerate(ordered):
            marginal[index] -= 2.0 * policy.concentration_penalty * sector_totals[candidate.sector]
        weights = project(weights + policy.step_size * marginal)

    forecast_vol = math.sqrt(max(0.0, float(weights @ cov @ weights)))
    weighted_cvar = float(weights @ cvar)
    sector_concentration = sum(float(weights[idx].sum()) ** 2 for idx in sector_groups.values())
    objective = (float(weights @ alpha) - policy.volatility_penalty * float(weights @ cov @ weights)
                 - policy.cvar_penalty * weighted_cvar
                 - policy.concentration_penalty * sector_concentration)
    weight_map = {candidate.candidate_id: float(weights[index]) for index, candidate in enumerate(ordered)}
    cash = max(0.0, 1.0 - sum(weight_map.values()))
    fields = {"weights": weight_map, "cash_weight": cash,
              "forecast_portfolio_volatility": forecast_vol,
              "weighted_cvar_loss": weighted_cvar, "objective_value": objective,
              "promotion_allowed": False}
    digest = hashlib.sha256(json.dumps(fields, sort_keys=True, separators=(",", ":"),
                                      allow_nan=False).encode()).hexdigest()
    return RiskAllocation(weight_map, cash, forecast_vol, weighted_cvar, objective, False, digest)
