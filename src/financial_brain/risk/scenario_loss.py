"""Worst credible position loss at intended size, including execution costs."""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Literal


class ScenarioLossError(ValueError):
    """Invalid size, scenario mark or cost input for a risk estimate."""


@dataclass(frozen=True, slots=True)
class AdverseScenario:
    scenario_id: str
    exit_price: float
    total_fees_inr: float


@dataclass(frozen=True, slots=True)
class PositionLossEstimate:
    side: Literal["LONG", "SHORT"]
    quantity: int
    multiplier: float
    entry_price: float
    worst_scenario_id: str
    gross_loss_inr: float
    fees_inr: float
    worst_net_loss_inr: float
    scenario_losses_inr: tuple[tuple[str, float], ...]


def worst_position_loss(
    *,
    side: Literal["LONG", "SHORT"],
    quantity: int,
    multiplier: float,
    entry_price: float,
    scenarios: tuple[AdverseScenario, ...],
) -> PositionLossEstimate:
    """Evaluate loss at the intended size; scenario exit prices include stress slippage.

    Fees are specified per scenario in INR and are added after scenario P&L. This is a
    conservative risk estimate, not a forecast, guarantee, or substitute for margin checks.
    """
    if side not in ("LONG", "SHORT") or quantity < 1:
        raise ScenarioLossError("side and positive intended quantity are required")
    if (not isfinite(multiplier) or multiplier <= 0 or not isfinite(entry_price)
            or entry_price <= 0 or not scenarios):
        raise ScenarioLossError("finite positive multiplier/entry and at least one scenario are required")
    seen: set[str] = set()
    outcomes: list[tuple[str, float, float, float]] = []
    for scenario in scenarios:
        if (not scenario.scenario_id or scenario.scenario_id in seen
                or not isfinite(scenario.exit_price) or scenario.exit_price <= 0
                or not isfinite(scenario.total_fees_inr) or scenario.total_fees_inr < 0):
            raise ScenarioLossError("scenario ids, exit prices or fee estimates are invalid")
        seen.add(scenario.scenario_id)
        pnl = ((scenario.exit_price - entry_price) if side == "LONG"
               else (entry_price - scenario.exit_price)) * quantity * multiplier
        gross_loss = max(0.0, -pnl)
        outcomes.append((scenario.scenario_id, gross_loss,
                         scenario.total_fees_inr, gross_loss + scenario.total_fees_inr))
    worst = max(outcomes, key=lambda row: (row[3], row[0]))
    return PositionLossEstimate(
        side=side, quantity=quantity, multiplier=multiplier, entry_price=entry_price,
        worst_scenario_id=worst[0], gross_loss_inr=worst[1], fees_inr=worst[2],
        worst_net_loss_inr=worst[3],
        scenario_losses_inr=tuple((row[0], row[3]) for row in outcomes))
