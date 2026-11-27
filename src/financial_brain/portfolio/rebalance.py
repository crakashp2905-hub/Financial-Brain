"""Paper-only, fixed-charge-aware rebalancing with explicit no-trade bands.

This planner proposes virtual weight changes. It never submits orders and expects the
caller to provide horizon-matched marginal benefit estimates and an approved cost schedule.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import math
from typing import Any, cast


class RebalanceError(ValueError):
    """Rebalance inputs are missing, inconsistent or invalid."""


@dataclass(frozen=True)
class RebalanceLine:
    instrument_id: str
    current_weight: float
    target_weight: float
    band_weight: float
    trade_weight: float
    trade_notional_inr: float
    proportional_cost_inr: float
    fixed_charge_inr: float
    total_cost_inr: float
    expected_improvement_inr: float
    expected_net_improvement_inr: float
    disposition: str


@dataclass(frozen=True)
class RebalancePlan:
    status: str
    nav_inr: float
    turnover_inr: float
    total_cost_inr: float
    expected_improvement_inr: float
    expected_net_improvement_inr: float
    proposed_weights: Mapping[str, float]
    lines: tuple[RebalanceLine, ...]


def _finite(value: object, label: str) -> float:
    if isinstance(value, bool):
        raise RebalanceError(f"{label} must be finite numeric data")
    try:
        number = float(cast(Any, value))
    except (TypeError, ValueError) as exc:
        raise RebalanceError(f"{label} must be finite numeric data") from exc
    if not math.isfinite(number):
        raise RebalanceError(f"{label} must be finite numeric data")
    return number


def plan_rebalance(
    current_weights: Mapping[str, float],
    target_weights: Mapping[str, float],
    nav_inr: float,
    *,
    no_trade_band_weights: Mapping[str, float],
    proportional_cost_rates: Mapping[str, float],
    fixed_charge_inr_per_order: Mapping[str, float],
    expected_improvement_inr: Mapping[str, float],
    turnover_budget_inr: float | None = None,
) -> RebalancePlan:
    """Propose target trades only when outside a band and benefit exceeds all costs.

    All mappings must cover the same full instrument universe. Weights are signed fractions
    of NAV; bands are absolute weight deviations. Proportional rates apply to absolute
    traded notional, while fixed charges apply once to each proposed instrument order.
    `expected_improvement_inr` and the cost schedule must refer to the same decision horizon.
    """
    if not isinstance(current_weights, Mapping) or not isinstance(target_weights, Mapping):
        raise RebalanceError("current and target weights must be mappings")
    names = sorted(set(current_weights) | set(target_weights))
    if not names or any(not isinstance(name, str) or not name.strip() for name in names):
        raise RebalanceError("instrument universe must contain non-empty identifiers")
    input_maps = (no_trade_band_weights, proportional_cost_rates,
                  fixed_charge_inr_per_order, expected_improvement_inr)
    if any(not isinstance(value, Mapping) or set(value) != set(names) for value in input_maps):
        raise RebalanceError("bands, costs and benefits must exactly cover the instrument universe")
    nav = _finite(nav_inr, "nav_inr")
    if nav <= 0:
        raise RebalanceError("nav_inr must be positive")
    turnover_budget = (_finite(turnover_budget_inr, "turnover_budget_inr")
                       if turnover_budget_inr is not None else None)
    if turnover_budget is not None and turnover_budget < 0:
        raise RebalanceError("turnover_budget_inr must be non-negative")

    lines: list[RebalanceLine] = []
    proposed: dict[str, float] = {}
    turnover = total_cost = gross_improvement = 0.0
    for name in names:
        current = _finite(current_weights.get(name, 0.0), f"current weight {name}")
        target = _finite(target_weights.get(name, 0.0), f"target weight {name}")
        band = _finite(no_trade_band_weights[name], f"no-trade band {name}")
        rate = _finite(proportional_cost_rates[name], f"proportional cost rate {name}")
        fixed = _finite(fixed_charge_inr_per_order[name], f"fixed charge {name}")
        benefit = _finite(expected_improvement_inr[name], f"expected improvement {name}")
        if band < 0 or not 0 <= rate < 1 or fixed < 0:
            raise RebalanceError("bands and fixed charges must be non-negative; cost rates in [0, 1)")
        delta = target - current
        if abs(delta) <= band + 1e-12:
            trade_weight = notional = prop_cost = charge = cost = net = 0.0
            disposition = "WITHIN_NO_TRADE_BAND"
            new_weight = current
        else:
            trade_weight = delta
            notional = abs(trade_weight) * nav
            prop_cost = notional * rate
            charge = fixed
            cost = prop_cost + charge
            net = benefit - cost
            if net <= 0:
                disposition = "REJECTED_COST_EXCEEDS_BENEFIT"
                new_weight = current
            else:
                disposition = "PAPER_TRADE_PROPOSED"
                new_weight = target
                turnover += notional
                total_cost += cost
                gross_improvement += benefit
        proposed[name] = new_weight
        lines.append(RebalanceLine(
            instrument_id=name,
            current_weight=current,
            target_weight=target,
            band_weight=band,
            trade_weight=trade_weight,
            trade_notional_inr=notional,
            proportional_cost_inr=prop_cost,
            fixed_charge_inr=charge,
            total_cost_inr=cost,
            expected_improvement_inr=benefit if disposition != "WITHIN_NO_TRADE_BAND" else 0.0,
            expected_net_improvement_inr=net,
            disposition=disposition,
        ))
    if turnover_budget is not None and turnover > turnover_budget:
        proposed = {name: _finite(current_weights.get(name, 0.0), f"current weight {name}")
                    for name in names}
        lines = [RebalanceLine(
            instrument_id=line.instrument_id,
            current_weight=line.current_weight,
            target_weight=line.target_weight,
            band_weight=line.band_weight,
            trade_weight=line.trade_weight,
            trade_notional_inr=line.trade_notional_inr,
            proportional_cost_inr=line.proportional_cost_inr,
            fixed_charge_inr=line.fixed_charge_inr,
            total_cost_inr=line.total_cost_inr,
            expected_improvement_inr=line.expected_improvement_inr,
            expected_net_improvement_inr=line.expected_net_improvement_inr,
            disposition=("REJECTED_TURNOVER_BUDGET" if line.disposition == "PAPER_TRADE_PROPOSED"
                         else line.disposition),
        ) for line in lines]
        return RebalancePlan(
            status="NO_ACTION", nav_inr=nav, turnover_inr=0.0, total_cost_inr=0.0,
            expected_improvement_inr=0.0, expected_net_improvement_inr=0.0,
            proposed_weights=proposed, lines=tuple(lines),
        )

    net_total = gross_improvement - total_cost
    status = "PAPER_REBALANCE_PROPOSED" if turnover else "NO_ACTION"
    return RebalancePlan(
        status=status,
        nav_inr=nav,
        turnover_inr=turnover,
        total_cost_inr=total_cost,
        expected_improvement_inr=gross_improvement,
        expected_net_improvement_inr=net_total,
        proposed_weights=proposed,
        lines=tuple(lines),
    )
