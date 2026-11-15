"""Alternative passive-fill outcomes under an unobserved queue-position interval."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
import math
from typing import cast


class QueueFillError(ValueError):
    """Frozen passive intentions do not contain a valid queue uncertainty range."""


_SCENARIOS = ("NO_FILL", "QUEUE_BEST_CASE", "QUEUE_MIDPOINT", "QUEUE_WORST_CASE", "FULL_FILL")


def _fill_quantity(*, quantity: int, queue_ahead: int, traded_through: int) -> int:
    return min(quantity, max(0, traded_through - queue_ahead))


def queue_fill_scenarios(
    intentions: Sequence[Mapping[str, object]],
    *,
    nav_inr: float,
) -> dict:
    """Aggregate scenario bounds without presenting queue estimates as observed fills.

    Each intention's trade-through volume and low/high queue estimates must be frozen
    inputs. Variable costs scale linearly by filled quantity. An optional fixed
    order-level charge is applied once to any nonzero fill; this makes partial-fill
    scenarios retain charges that do not shrink with executed quantity.
    """
    if not intentions:
        raise QueueFillError("at least one frozen passive intention is required")
    if not math.isfinite(nav_inr) or nav_inr <= 0:
        raise QueueFillError("nav_inr must be finite and positive")
    normalized: list[dict[str, float | int | str]] = []
    seen: set[str] = set()
    for row in intentions:
        identity = row.get("intention_id")
        if not isinstance(identity, str) or not identity.strip() or identity in seen:
            raise QueueFillError("intention_id must be present and unique")
        seen.add(identity)
        try:
            quantity = row["order_quantity"]
            queue_min = row["queue_ahead_min"]
            queue_max = row["queue_ahead_max"]
            traded = row["traded_through_quantity"]
            gross = float(cast(float, row["gross_pnl_full_fill_inr"]))
            cost = float(cast(float, row["cost_full_fill_inr"]))
            fixed_order_cost = float(cast(float, row.get("fixed_order_cost_inr", 0.0)))
        except (KeyError, TypeError, ValueError) as exc:
            raise QueueFillError(f"intention {identity} is missing queue or P&L inputs") from exc
        if any(type(value) is not int for value in (quantity, queue_min, queue_max, traded)):
            raise QueueFillError("order, queue and trade-through quantities must be integers")
        quantity, queue_min, queue_max, traded = (
            cast(int, value) for value in (quantity, queue_min, queue_max, traded)
        )
        if quantity <= 0 or queue_min < 0 or queue_max < queue_min or traded < 0:
            raise QueueFillError(f"intention {identity} has invalid queue bounds")
        if (not math.isfinite(gross) or not math.isfinite(cost) or cost < 0
                or not math.isfinite(fixed_order_cost) or fixed_order_cost < 0):
            raise QueueFillError(f"intention {identity} has invalid gross P&L or cost")
        normalized.append({"id": identity, "quantity": quantity, "queue_min": queue_min,
                           "queue_max": queue_max, "traded": traded, "gross": gross,
                           "cost": cost, "fixed_order_cost": fixed_order_cost})

    source = sorted(normalized, key=lambda item: str(item["id"]))
    source_hash = hashlib.sha256(json.dumps(source, sort_keys=True, separators=(",", ":"),
                                             allow_nan=False).encode()).hexdigest()
    totals = {name: {"gross_pnl_inr": 0.0, "execution_cost_inr": 0.0,
                     "filled_quantity": 0, "intended_quantity": 0} for name in _SCENARIOS}
    rows = []
    for item in normalized:
        quantity = int(item["quantity"])
        best = _fill_quantity(quantity=quantity, queue_ahead=int(item["queue_min"]),
                              traded_through=int(item["traded"]))
        worst = _fill_quantity(quantity=quantity, queue_ahead=int(item["queue_max"]),
                               traded_through=int(item["traded"]))
        midpoint_queue = (int(item["queue_min"]) + int(item["queue_max"])) // 2
        midpoint = _fill_quantity(quantity=quantity, queue_ahead=midpoint_queue,
                                  traded_through=int(item["traded"]))
        filled = {"NO_FILL": 0, "QUEUE_BEST_CASE": best, "QUEUE_MIDPOINT": midpoint,
                  "QUEUE_WORST_CASE": worst, "FULL_FILL": quantity}
        row_scenarios = {}
        for name, filled_quantity in filled.items():
            fraction = filled_quantity / quantity
            gross = float(item["gross"]) * fraction
            variable_cost = float(item["cost"]) * fraction
            fixed_cost = float(item["fixed_order_cost"]) if filled_quantity else 0.0
            cost = variable_cost + fixed_cost
            total = totals[name]
            total["gross_pnl_inr"] += gross
            total["execution_cost_inr"] += cost
            total["filled_quantity"] += filled_quantity
            total["intended_quantity"] += quantity
            row_scenarios[name] = {"fill_fraction": fraction, "filled_quantity": filled_quantity,
                                   "gross_pnl_inr": gross,
                                   "variable_execution_cost_inr": variable_cost,
                                   "fixed_order_cost_inr": fixed_cost,
                                   "execution_cost_inr": cost,
                                   "net_pnl_inr": gross - cost}
        rows.append({"intention_id": item["id"], "queue_ahead_interval": [item["queue_min"], item["queue_max"]],
                     "traded_through_quantity": item["traded"], "scenarios": row_scenarios})

    scenario_totals = {}
    for name, values in totals.items():
        net = values["gross_pnl_inr"] - values["execution_cost_inr"]
        intended = int(values["intended_quantity"])
        scenario_totals[name] = {
            **values,
            "net_pnl_inr": net,
            "net_return_nav_pct": net / nav_inr * 100,
            "aggregate_fill_rate": int(values["filled_quantity"]) / intended,
        }
    queue_endpoint_names = ("QUEUE_BEST_CASE", "QUEUE_WORST_CASE")
    queue_lower_name = min(queue_endpoint_names,
                           key=lambda name: float(scenario_totals[name]["net_pnl_inr"]))
    queue_upper_name = max(queue_endpoint_names,
                           key=lambda name: float(scenario_totals[name]["net_pnl_inr"]))
    minimum = min(scenario_totals.values(), key=lambda value: float(value["net_pnl_inr"]))
    maximum = max(scenario_totals.values(), key=lambda value: float(value["net_pnl_inr"]))
    return {
        "evidence_class": "QUEUE_POSITION_SCENARIO",
        "intention_count": len(normalized),
        "intention_population_sha256": source_hash,
        "assumption": "Trade-through volume is applied to low/mid/high queue-ahead estimates; gross and variable costs scale linearly with filled quantity, while fixed_order_cost_inr is charged once for any nonzero fill. One intention represents one order; order-level charges are not multiplied for multiple partial executions. These are bounds under supplied inputs, not observed broker fills.",
        "scenario_totals": scenario_totals,
        "queue_position_net_pnl_bounds_inr": {
            "lower": scenario_totals[queue_lower_name]["net_pnl_inr"],
            "lower_scenario": queue_lower_name,
            "upper": scenario_totals[queue_upper_name]["net_pnl_inr"],
            "upper_scenario": queue_upper_name,
        },
        "best_case_net_pnl_inr": maximum["net_pnl_inr"],
        "worst_case_net_pnl_inr": minimum["net_pnl_inr"],
        "outcome_bounds_source": "min/max across no-fill, queue interval and full-fill scenario totals; economics need not be monotonic in fill rate",
        "intentions": rows,
    }
