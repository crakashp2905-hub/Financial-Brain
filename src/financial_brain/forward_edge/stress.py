"""Joint after-cost stress scenarios for fills, outages and missed opportunities (FE-0099)."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Mapping


class StressError(ValueError):
    """Invalid cost, fill or outage stress input."""


@dataclass(frozen=True)
class StressTrade:
    opportunity_id: str
    notional: float
    gross_pnl: float
    cost_components: Mapping[str, float]
    outage_exposed: bool

    def __post_init__(self) -> None:
        if not self.opportunity_id.strip():
            raise StressError("opportunity_id is required")
        if not math.isfinite(self.notional) or self.notional <= 0 or not math.isfinite(self.gross_pnl):
            raise StressError("trade notional must be positive and P&L finite")
        if any(not name.strip() or not math.isfinite(value) or value < 0
               for name, value in self.cost_components.items()):
            raise StressError("cost components require names and finite non-negative amounts")


@dataclass(frozen=True)
class ExecutionStressScenario:
    scenario_id: str
    cost_multiplier: float
    adverse_fill_bps: float
    normal_fill_fraction: float
    outage_fill_fraction: float

    def __post_init__(self) -> None:
        if not self.scenario_id.strip():
            raise StressError("scenario ID is required")
        if not math.isfinite(self.cost_multiplier) or self.cost_multiplier < 1:
            raise StressError("cost multiplier must be at least one")
        if not math.isfinite(self.adverse_fill_bps) or self.adverse_fill_bps < 0:
            raise StressError("adverse fill bps cannot be negative")
        if any(not math.isfinite(value) or not 0 <= value <= 1
               for value in (self.normal_fill_fraction, self.outage_fill_fraction)):
            raise StressError("fill fractions must be between zero and one")


@dataclass(frozen=True)
class StressResult:
    scenario_id: str
    opportunities: int
    filled_fraction: float
    missed_fill_count: int
    gross_pnl: float
    execution_costs: float
    net_pnl: float
    net_return_on_starting_capital: float
    foregone_counterfactual_net_pnl: float


@dataclass(frozen=True)
class StressReport:
    starting_capital: float
    baseline_net_pnl: float
    scenarios: tuple[StressResult, ...]
    report_hash: str


def evaluate_execution_stress(trades: tuple[StressTrade, ...],
                              scenarios: tuple[ExecutionStressScenario, ...], *,
                              starting_capital: float) -> StressReport:
    """Apply explicit deterministic execution stresses; no fill assumptions are calibrated here."""
    if not math.isfinite(starting_capital) or starting_capital <= 0:
        raise StressError("starting capital must be finite and positive")
    if not trades or not scenarios:
        raise StressError("trade cohort and stress scenarios must be non-empty")
    if len({trade.opportunity_id for trade in trades}) != len(trades):
        raise StressError("opportunity IDs must be unique")
    if len({scenario.scenario_id for scenario in scenarios}) != len(scenarios):
        raise StressError("scenario IDs must be unique")
    baseline_scenarios = [scenario for scenario in scenarios if scenario.scenario_id == "BASELINE"]
    if len(baseline_scenarios) != 1 or baseline_scenarios[0] != ExecutionStressScenario("BASELINE", 1.0, 0.0, 1.0, 1.0):
        raise StressError("include exactly one explicit un-stressed BASELINE scenario")
    results = []
    baseline = None
    for scenario in scenarios:
        gross = costs = foregone = filled_notional = total_notional = 0.0
        missed = 0
        for trade in trades:
            fill = scenario.outage_fill_fraction if trade.outage_exposed else scenario.normal_fill_fraction
            fill_units = trade.notional * fill
            filled_notional += fill_units
            total_notional += trade.notional
            if fill < 1.0:
                missed += 1
            trade_cost = sum(trade.cost_components.values()) * fill * scenario.cost_multiplier
            trade_cost += fill_units * scenario.adverse_fill_bps / 10_000
            trade_gross = trade.gross_pnl * fill
            gross += trade_gross
            costs += trade_cost
            foregone += (trade.gross_pnl - sum(trade.cost_components.values())) - (trade_gross - trade_cost)
        net = gross - costs
        if scenario.scenario_id == "BASELINE":
            baseline = net
        results.append(StressResult(scenario.scenario_id, len(trades), filled_notional / total_notional,
                                    missed, gross, costs, net, net / starting_capital, foregone))
    baseline_net = baseline if baseline is not None else results[0].net_pnl
    fields = {"starting_capital": starting_capital, "baseline_net_pnl": baseline_net,
              "scenarios": [result.__dict__ for result in results]}
    digest = hashlib.sha256(json.dumps(fields, sort_keys=True, separators=(",", ":"),
                                      allow_nan=False).encode()).hexdigest()
    return StressReport(starting_capital, baseline_net, tuple(results), digest)
