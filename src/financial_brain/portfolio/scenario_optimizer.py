"""Linear scenario-constrained allocation under explicit Indian portfolio stress panels.

Scenario returns and funding multipliers are caller-supplied, source-hashed assumptions.
This optimizer is a paper/research proposal only and cannot create orders or promote a book.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping

import numpy as np
from scipy.optimize import linprog

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ScenarioOptimizationError(ValueError):
    """Invalid scenario set, candidate panel or optimization constraints."""


class ScenarioKind(StrEnum):
    SECTOR_SHOCK = "SECTOR_SHOCK"
    INDEX_CRASH = "INDEX_CRASH"
    OPTION_GAMMA_SHOCK = "OPTION_GAMMA_SHOCK"
    FUNDING_STRESS = "FUNDING_STRESS"


_REQUIRED_KINDS = frozenset(ScenarioKind)


def _utc(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ScenarioOptimizationError(f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _hash(value: str, field: str) -> None:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ScenarioOptimizationError(f"{field} must be a lowercase SHA-256")


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class ScenarioCandidate:
    candidate_id: str
    expected_net_return: float
    funding_fraction: float
    maximum_weight: float
    evidence_sha256: str

    def __post_init__(self) -> None:
        if not self.candidate_id.strip():
            raise ScenarioOptimizationError("candidate ID is required")
        if not math.isfinite(self.expected_net_return) or self.expected_net_return <= -1:
            raise ScenarioOptimizationError("expected net return must be finite and above -100%")
        if not math.isfinite(self.funding_fraction) or self.funding_fraction < 0:
            raise ScenarioOptimizationError("funding fraction must be finite and non-negative")
        if not math.isfinite(self.maximum_weight) or not 0 < self.maximum_weight <= 1:
            raise ScenarioOptimizationError("maximum candidate weight must be in (0, 1]")
        _hash(self.evidence_sha256, "candidate evidence hash")


@dataclass(frozen=True, slots=True)
class StressScenario:
    scenario_id: str
    kind: ScenarioKind
    as_of: datetime
    candidate_returns: Mapping[str, float]
    funding_multiplier: float
    maximum_loss_fraction: float
    evidence_sha256: str

    def __post_init__(self) -> None:
        if not self.scenario_id.strip() or not isinstance(self.kind, ScenarioKind):
            raise ScenarioOptimizationError("scenario identity and typed scenario kind are required")
        object.__setattr__(self, "as_of", _utc(self.as_of, "scenario as_of"))
        if not self.candidate_returns or any(not key.strip() or not math.isfinite(value)
                                             for key, value in self.candidate_returns.items()):
            raise ScenarioOptimizationError("scenario returns must be finite and cover named candidates")
        if not math.isfinite(self.funding_multiplier) or self.funding_multiplier <= 0:
            raise ScenarioOptimizationError("funding multiplier must be finite and positive")
        if not math.isfinite(self.maximum_loss_fraction) or not 0 < self.maximum_loss_fraction < 1:
            raise ScenarioOptimizationError("scenario loss limit must be in (0, 1)")
        _hash(self.evidence_sha256, "scenario evidence hash")
        object.__setattr__(self, "candidate_returns", MappingProxyType(dict(sorted(self.candidate_returns.items()))))


@dataclass(frozen=True, slots=True)
class ScenarioAllocation:
    weights: Mapping[str, float]
    cash_weight: float
    expected_net_return: float
    expected_excess_return_vs_cash: float
    cash_return_hurdle: float
    scenario_returns: Mapping[str, float]
    stressed_funding_fraction: float
    scenario_set_sha256: str
    allocation_sha256: str
    promotion_allowed: bool = False


def optimize_scenarios(*, candidates: tuple[ScenarioCandidate, ...],
                       scenarios: tuple[StressScenario, ...], as_of: datetime,
                       funding_capacity_fraction: float, cash_return_hurdle: float,
                       maximum_gross_weight: float = 1.0
                       ) -> ScenarioAllocation:
    """Maximize expected net excess return over cash under joint stress/funding constraints.

    The scenario panel must include sector, index-crash, option-gamma and funding stresses.
    Scenario vectors must cover every candidate with the same as-of timestamp. The optimizer
    has no broker surface; it returns weights and leaves all unused budget as cash.
    """
    now = _utc(as_of, "as_of")
    if not candidates or len({row.candidate_id for row in candidates}) != len(candidates):
        raise ScenarioOptimizationError("candidate panel must be non-empty with unique IDs")
    if not scenarios or len({row.scenario_id for row in scenarios}) != len(scenarios):
        raise ScenarioOptimizationError("scenario panel must be non-empty with unique IDs")
    if not math.isfinite(funding_capacity_fraction) or funding_capacity_fraction < 0:
        raise ScenarioOptimizationError("funding capacity must be finite and non-negative")
    if not math.isfinite(maximum_gross_weight) or not 0 < maximum_gross_weight <= 1:
        raise ScenarioOptimizationError("maximum gross weight must be in (0, 1]")

    ordered = tuple(sorted(candidates, key=lambda row: row.candidate_id))
    candidate_ids = {row.candidate_id for row in ordered}
    ordered_scenarios = tuple(sorted(scenarios, key=lambda row: row.scenario_id))
    if any(set(row.candidate_returns) != candidate_ids for row in ordered_scenarios):
        raise ScenarioOptimizationError("each scenario must cover exactly the frozen candidate set")
    if any(row.as_of != now for row in ordered_scenarios):
        raise ScenarioOptimizationError("scenario observations must share the allocation as-of clock")
    missing_kinds = _REQUIRED_KINDS - {row.kind for row in ordered_scenarios}
    if missing_kinds:
        raise ScenarioOptimizationError(f"required scenario types are missing: {sorted(x.value for x in missing_kinds)}")

    if not math.isfinite(cash_return_hurdle) or cash_return_hurdle <= -1:
        raise ScenarioOptimizationError("cash return hurdle must be finite and above -100%")
    objective = -np.asarray([row.expected_net_return - cash_return_hurdle for row in ordered], dtype=float)
    funding = np.asarray([row.funding_fraction for row in ordered], dtype=float)
    matrix: list[list[float]] = []
    limits: list[float] = []
    for scenario in ordered_scenarios:
        returns = np.asarray([scenario.candidate_returns[row.candidate_id] for row in ordered])
        matrix.append((-returns).tolist())
        limits.append(scenario.maximum_loss_fraction)
        if scenario.kind is ScenarioKind.FUNDING_STRESS:
            matrix.append((funding * scenario.funding_multiplier).tolist())
            limits.append(funding_capacity_fraction)
    matrix.append([1.0] * len(ordered))
    limits.append(maximum_gross_weight)
    result = linprog(objective, A_ub=np.asarray(matrix), b_ub=np.asarray(limits),
                     bounds=[(0.0, row.maximum_weight) for row in ordered], method="highs")
    if not result.success or result.x is None:
        raise ScenarioOptimizationError(f"scenario allocation infeasible: {result.message}")

    weight_map = {row.candidate_id: max(0.0, float(result.x[index]))
                  for index, row in enumerate(ordered) if result.x[index] > 1e-12}
    total_weight = sum(weight_map.values())
    cash = max(0.0, 1.0 - total_weight)
    scenario_returns = {
        row.scenario_id: sum(weight_map.get(candidate.candidate_id, 0.0)
                             * row.candidate_returns[candidate.candidate_id] for candidate in ordered)
        for row in ordered_scenarios
    }
    stressed_funding = max((sum(weight_map.get(candidate.candidate_id, 0.0)
                                * candidate.funding_fraction * row.funding_multiplier for candidate in ordered)
                            for row in ordered_scenarios), default=0.0)
    expected = sum(weight_map.get(row.candidate_id, 0.0) * row.expected_net_return for row in ordered)
    excess = expected - cash_return_hurdle * total_weight
    source_panel = {"as_of": now.isoformat(),
                    "candidates": [{"id": row.candidate_id, "evidence_sha256": row.evidence_sha256,
                                    "expected_net_return": row.expected_net_return,
                                    "funding_fraction": row.funding_fraction,
                                    "maximum_weight": row.maximum_weight} for row in ordered],
                    "scenarios": [{"id": row.scenario_id, "kind": row.kind.value,
                                   "evidence_sha256": row.evidence_sha256,
                                   "candidate_returns": dict(row.candidate_returns),
                                   "funding_multiplier": row.funding_multiplier,
                                   "maximum_loss_fraction": row.maximum_loss_fraction}
                                  for row in ordered_scenarios]}
    scenario_hash = _digest(source_panel)
    report = {"weights": weight_map, "cash_weight": cash, "expected_net_return": expected,
              "expected_excess_return_vs_cash": excess, "cash_return_hurdle": cash_return_hurdle,
              "scenario_returns": scenario_returns, "stressed_funding_fraction": stressed_funding,
              "scenario_set_sha256": scenario_hash, "promotion_allowed": False}
    return ScenarioAllocation(MappingProxyType(weight_map), cash, expected, excess, cash_return_hurdle,
                              MappingProxyType(dict(sorted(scenario_returns.items()))),
                              stressed_funding, scenario_hash, _digest(report))


__all__ = ["ScenarioAllocation", "ScenarioCandidate", "ScenarioKind", "ScenarioOptimizationError",
           "StressScenario", "optimize_scenarios"]
