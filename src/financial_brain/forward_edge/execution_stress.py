"""Intention-level execution cost and fill-rate stress reports.

The input is a frozen intention population, including opportunities that were missed
or only partially filled. Each row records the full-intention gross outcome and its
full-intention execution cost; scenario fill rates scale both linearly as an explicit
stress assumption. The output is a scenario analysis, not a broker-fill observation.
"""
from __future__ import annotations

import hashlib
import json
from math import isfinite
from typing import Any

from .evidence_stats import EXECUTION_STRESS_COSTS, EXECUTION_STRESS_FILL_RATES, execution_stress_gate


class ExecutionStressError(ValueError):
    """The frozen intention population is malformed or incomplete."""


def build_execution_stress_report(intentions: list[dict[str, Any]], *, nav_inr: float) -> dict[str, Any]:
    """Compute the full cost × fill-rate grid from every frozen intention.

    Required row fields are unique ``intention_id``, finite
    ``gross_pnl_full_fill_inr``, and finite non-negative ``cost_full_fill_inr``.
    Missing rows/data are rejected instead of silently dropping missed fills from
    the denominator. Costs are stressed multiplicatively and both gross and costs
    scale with the scenario fill rate. A caller must use observed fill outcomes to
    replace this scenario assumption before claiming empirical execution results.
    """
    if not intentions:
        raise ExecutionStressError("at least one frozen intention is required")
    if not isfinite(nav_inr) or nav_inr <= 0:
        raise ExecutionStressError("nav_inr must be finite and positive")

    normalized: list[tuple[str, float, float]] = []
    seen: set[str] = set()
    for row in intentions:
        identity = row.get("intention_id")
        if not isinstance(identity, str) or not identity.strip() or identity in seen:
            raise ExecutionStressError("intention_id must be present and unique")
        seen.add(identity)
        try:
            gross = float(row["gross_pnl_full_fill_inr"])
            cost = float(row["cost_full_fill_inr"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ExecutionStressError(f"intention {identity} is missing numeric gross/cost inputs") from exc
        if not isfinite(gross) or not isfinite(cost) or cost < 0:
            raise ExecutionStressError(f"intention {identity} has invalid gross/cost inputs")
        normalized.append((identity, gross, cost))

    source = [{"intention_id": identity, "gross_pnl_full_fill_inr": gross, "cost_full_fill_inr": cost}
              for identity, gross, cost in sorted(normalized)]
    source_hash = hashlib.sha256(json.dumps(source, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    grid: dict[str, dict[str, float]] = {}
    net_by_cell: dict[tuple[float, float], float] = {}
    gross_full = sum(row[1] for row in normalized)
    costs_full = sum(row[2] for row in normalized)
    for multiplier in EXECUTION_STRESS_COSTS:
        for fill_rate in EXECUTION_STRESS_FILL_RATES:
            gross = gross_full * fill_rate
            cost = costs_full * multiplier * fill_rate
            net = gross - cost
            net_by_cell[(multiplier, fill_rate)] = net
            grid[f"{multiplier:g}x@{fill_rate:.0%}"] = {
                "gross_pnl_inr": gross,
                "execution_cost_inr": cost,
                "net_pnl_inr": net,
                "net_return_nav_pct": net / nav_inr * 100.0,
            }
    gate = execution_stress_gate(net_by_cell)
    return {
        "evidence_class": "SCENARIO_STRESS",
        "intention_count": len(normalized),
        "intention_population_sha256": source_hash,
        "assumption": "Uniform fill-rate scaling of frozen full-intention gross and costs; not observed execution.",
        "cost_multipliers": list(EXECUTION_STRESS_COSTS),
        "fill_rates": list(EXECUTION_STRESS_FILL_RATES),
        "scenarios": grid,
        "robust_economics": gate["status"] == "PASS",
        "promotion_gate": gate,
    }
