"""Joint cost/fill sensitivity and market-beta dependence diagnostics."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass

from financial_brain.evaluation.riskadjusted import capm


class ProfitRobustnessError(ValueError):
    """Invalid or non-comparable market/cost sensitivity inputs."""


@dataclass(frozen=True)
class NetReturnScenario:
    scenario_id: str
    cost_multiplier: float
    slippage_multiplier: float
    fill_evidence: str  # OBSERVED_QUOTE, BASE_ESTIMATE, OPTIMISTIC_ESTIMATE
    net_returns: tuple[float, ...]


def evaluate_profit_robustness(*, scenarios: tuple[NetReturnScenario, ...],
                               benchmark_returns: tuple[float, ...],
                               horizon_sessions: int = 20,
                               extraordinary_beta_threshold: float = 1.20) -> dict[str, object]:
    """Re-estimate market alpha for each net cost/fill scenario and surface fragile gains."""
    if len(scenarios) < 2 or not benchmark_returns:
        raise ProfitRobustnessError("at least two scenarios and a benchmark series are required")
    ids = [row.scenario_id for row in scenarios]
    if len(set(ids)) != len(ids) or any(not value.strip() for value in ids):
        raise ProfitRobustnessError("scenario IDs must be unique and non-empty")
    if type(horizon_sessions) is not int or horizon_sessions <= 0:
        raise ProfitRobustnessError("horizon_sessions must be a positive integer")
    if not math.isfinite(extraordinary_beta_threshold) or extraordinary_beta_threshold < 0:
        raise ProfitRobustnessError("extraordinary beta threshold must be finite and non-negative")
    n = len(benchmark_returns)
    values = [*benchmark_returns, extraordinary_beta_threshold,
              *(value for row in scenarios for value in
                (row.cost_multiplier, row.slippage_multiplier, *row.net_returns))]
    if any(not math.isfinite(value) for value in values):
        raise ProfitRobustnessError("all scenario and benchmark values must be finite")
    if any(value <= -1 for value in benchmark_returns):
        raise ProfitRobustnessError("benchmark return cannot exhaust equity")
    if any(len(row.net_returns) != n for row in scenarios):
        raise ProfitRobustnessError("all scenario returns must align to the benchmark sessions")
    if any(row.cost_multiplier < 1 or row.slippage_multiplier < 1
           or any(value <= -1 for value in row.net_returns) for row in scenarios):
        raise ProfitRobustnessError("stress multipliers must be at least one and returns above -100 percent")
    allowed_evidence = {"OBSERVED_QUOTE", "BASE_ESTIMATE", "OPTIMISTIC_ESTIMATE"}
    if any(row.fill_evidence not in allowed_evidence for row in scenarios):
        raise ProfitRobustnessError("fill evidence class must be explicit")
    if not any(row.cost_multiplier == 1 and row.slippage_multiplier == 1 for row in scenarios):
        raise ProfitRobustnessError("an exact base cost/slippage scenario is required")

    reports = []
    for row in scenarios:
        regression = capm(list(row.net_returns), list(benchmark_returns), horizon=horizon_sessions)
        raw_alpha = regression["alpha"]
        raw_beta = regression["beta"]
        raw_alpha_t = regression["alpha_t"]
        alpha = (float(raw_alpha) if isinstance(raw_alpha, (float, int))
                 and math.isfinite(float(raw_alpha)) else None)
        beta = (float(raw_beta) if isinstance(raw_beta, (float, int))
                and math.isfinite(float(raw_beta)) else None)
        alpha_t = (float(raw_alpha_t) if isinstance(raw_alpha_t, (float, int))
                   and math.isfinite(float(raw_alpha_t)) else None)
        mean_return = sum(row.net_returns) / len(row.net_returns)
        reports.append({
            "scenario_id": row.scenario_id,
            "cost_multiplier": row.cost_multiplier,
            "slippage_multiplier": row.slippage_multiplier,
            "fill_evidence": row.fill_evidence,
            "mean_net_return": mean_return,
            "compounded_net_return": math.prod(1 + value for value in row.net_returns) - 1,
            "market_alpha": alpha,
            "market_beta": beta,
            "alpha_t": alpha_t,
            "market_exposure_dependent": bool(
                alpha is not None and beta is not None and mean_return > 0
                and alpha <= 0 and beta > extraordinary_beta_threshold),
        })
    positive = []
    for result in reports:
        compounded = result["compounded_net_return"]
        if isinstance(compounded, (float, int)) and compounded > 0:
            positive.append(result)
    conservative = [row for row in positive if row["fill_evidence"] != "OPTIMISTIC_ESTIMATE"]
    optimistic_dependency = bool(positive and not conservative)
    base = next(row for row in reports if row["cost_multiplier"] == 1
                and row["slippage_multiplier"] == 1)
    base_return = base["compounded_net_return"]
    body: dict[str, object] = {
        "scenario_count": len(reports),
        "benchmark_sessions": n,
        "horizon_sessions": horizon_sessions,
        "base_scenario_id": base["scenario_id"],
        "base_profit_positive": isinstance(base_return, (float, int)) and base_return > 0,
        "positive_scenario_count": len(positive),
        "optimistic_fill_dependency": optimistic_dependency,
        "market_exposure_dependency_in_any_profitable_scenario": any(
            row.get("market_exposure_dependent") is True for row in positive),
        "scenarios": reports,
        "interpretation": "market alpha uses Newey-West CAPM; cost/fill inputs and benchmark construction remain caller supplied",
        "promotion_authorized": False,
    }
    body["report_sha256"] = hashlib.sha256(json.dumps(
        body, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()).hexdigest()
    return body
