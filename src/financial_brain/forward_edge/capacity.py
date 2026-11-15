"""Liquidity-linked capacity curves with explicit calibration and coverage gates (FE-0096)."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass


class CapacityError(ValueError):
    """Invalid liquidity, cost calibration or capacity scenario."""


@dataclass(frozen=True)
class CapacityCostPoint:
    participation_rate: float
    total_execution_cost_bps: float
    observed_trades: int

    def __post_init__(self) -> None:
        if not math.isfinite(self.participation_rate) or self.participation_rate <= 0:
            raise CapacityError("participation rate must be finite and positive")
        if not math.isfinite(self.total_execution_cost_bps) or self.total_execution_cost_bps < 0:
            raise CapacityError("execution cost must be finite and non-negative")
        if type(self.observed_trades) is not int or self.observed_trades < 1:
            raise CapacityError("cost point requires a positive observed trade count")


@dataclass(frozen=True)
class CapacityScenario:
    notional: float
    participation_rate: float
    cost_bps: float | None
    expected_net_alpha_bps: float | None
    within_visible_depth: bool
    supported_by_calibration: bool


@dataclass(frozen=True)
class CapacityReport:
    scenarios: tuple[CapacityScenario, ...]
    recommended_max_notional: float | None
    recommendation_status: str
    source_hash: str
    report_hash: str


def evaluate_capacity(*, gross_alpha_bps: float, adv_notional: float,
                      visible_depth_notional: float, notionals: tuple[float, ...],
                      cost_curve: tuple[CapacityCostPoint, ...], source_hash: str,
                      owner_attested_live_observations: bool,
                      minimum_trades_per_point: int = 30,
                      minimum_quote_coverage: float = 0.95,
                      observed_quote_coverage: float = 0.0) -> CapacityReport:
    """Interpolate only within observed cost points; never invent costs beyond calibration.

    Gross alpha is held constant as an explicit scenario assumption. A size ceiling is
    conditional on caller-attested live calibration and visible depth, not an order approval.
    """
    for name, value in (("gross_alpha_bps", gross_alpha_bps), ("adv_notional", adv_notional),
                        ("visible_depth_notional", visible_depth_notional),
                        ("observed_quote_coverage", observed_quote_coverage),
                        ("minimum_quote_coverage", minimum_quote_coverage)):
        if not math.isfinite(value):
            raise CapacityError(f"{name} must be finite")
    if adv_notional <= 0 or visible_depth_notional < 0:
        raise CapacityError("ADV must be positive and visible depth cannot be negative")
    if not source_hash.strip():
        raise CapacityError("calibration source hash is required")
    if type(minimum_trades_per_point) is not int or minimum_trades_per_point < 1:
        raise CapacityError("minimum_trades_per_point must be positive")
    if not 0 < minimum_quote_coverage <= 1 or not 0 <= observed_quote_coverage <= 1:
        raise CapacityError("quote coverage must be in (0, 1]")
    if not notionals or any(not math.isfinite(n) or n <= 0 for n in notionals):
        raise CapacityError("candidate notionals must be finite and positive")
    if len(set(notionals)) != len(notionals):
        raise CapacityError("candidate notionals must be unique")
    points = tuple(sorted(cost_curve, key=lambda point: point.participation_rate))
    if len(points) < 2 or len({point.participation_rate for point in points}) != len(points):
        raise CapacityError("at least two distinct observed participation cost points are required")
    if any(a.total_execution_cost_bps > b.total_execution_cost_bps for a, b in zip(points, points[1:])):
        raise CapacityError("observed execution cost curve must be non-decreasing")

    coverage_ok = (observed_quote_coverage >= minimum_quote_coverage
                   and all(point.observed_trades >= minimum_trades_per_point for point in points))
    scenarios = []
    for notional in sorted(notionals):
        participation = notional / adv_notional
        cost = None
        supported = False
        if points[0].participation_rate <= participation <= points[-1].participation_rate:
            for left, right in zip(points, points[1:]):
                if left.participation_rate <= participation <= right.participation_rate:
                    weight = ((participation - left.participation_rate)
                              / (right.participation_rate - left.participation_rate))
                    cost = left.total_execution_cost_bps + weight * (
                        right.total_execution_cost_bps - left.total_execution_cost_bps)
                    supported = coverage_ok
                    break
            if participation == points[-1].participation_rate:
                cost = points[-1].total_execution_cost_bps
                supported = coverage_ok
        feasible_depth = notional <= visible_depth_notional
        net = gross_alpha_bps - cost if cost is not None else None
        scenarios.append(CapacityScenario(notional, participation, cost, net,
                                          feasible_depth, supported))
    eligible = [scenario.notional for scenario in scenarios
                if scenario.within_visible_depth and scenario.supported_by_calibration
                and scenario.expected_net_alpha_bps is not None and scenario.expected_net_alpha_bps > 0]
    recommendation = max(eligible) if eligible and owner_attested_live_observations else None
    if not owner_attested_live_observations:
        status = "NO_SIZE_RECOMMENDATION_FIXTURE_OR_UNATTESTED_CALIBRATION"
    elif not coverage_ok:
        status = "NO_SIZE_RECOMMENDATION_INSUFFICIENT_TRADE_OR_QUOTE_COVERAGE"
    elif recommendation is None:
        status = "NO_POSITIVE_NET_ALPHA_WITHIN_SUPPORTED_VISIBLE_DEPTH"
    else:
        status = "CONDITIONAL_MAX_NOTIONAL_CALLER_ATTESTED_CALIBRATION_ONLY"
    fields = {"scenarios": [{"notional": row.notional, "participation_rate": row.participation_rate,
                            "cost_bps": row.cost_bps, "expected_net_alpha_bps": row.expected_net_alpha_bps,
                            "within_visible_depth": row.within_visible_depth,
                            "supported_by_calibration": row.supported_by_calibration} for row in scenarios],
              "recommended_max_notional": recommendation, "recommendation_status": status,
              "source_hash": source_hash}
    digest = hashlib.sha256(json.dumps(fields, sort_keys=True, separators=(",", ":"),
                                      allow_nan=False).encode()).hexdigest()
    return CapacityReport(tuple(scenarios), recommendation, status, source_hash, digest)
