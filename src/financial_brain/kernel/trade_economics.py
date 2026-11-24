"""Planned and after-cost trade-plan economics; estimates are not guarantees."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from .trader_signal import TradePlan


class TradeEconomicsError(ValueError):
    """Invalid cost or trade-plan economics input."""


def _nonnegative(name: str, raw: Decimal | str | int) -> Decimal:
    try:
        value = raw if isinstance(raw, Decimal) else Decimal(str(raw))
    except (InvalidOperation, ValueError) as exc:
        raise TradeEconomicsError(f"{name} must be decimal") from exc
    if not value.is_finite() or value < 0:
        raise TradeEconomicsError(f"{name} must be finite and non-negative")
    return value


@dataclass(frozen=True, slots=True)
class TradingCosts:
    spread_inr: Decimal
    fees_inr: Decimal
    impact_inr: Decimal
    taxes_inr: Decimal
    financing_inr: Decimal = Decimal(0)

    def __post_init__(self) -> None:
        for name in ("spread_inr", "fees_inr", "impact_inr", "taxes_inr", "financing_inr"):
            object.__setattr__(self, name, _nonnegative(name, getattr(self, name)))

    @property
    def total_inr(self) -> Decimal:
        return sum((self.spread_inr, self.fees_inr, self.impact_inr,
                    self.taxes_inr, self.financing_inr), Decimal(0))


@dataclass(frozen=True, slots=True)
class TradeEconomics:
    entry_reference: Decimal
    planned_loss_inr: Decimal
    gross_reward_inr: Decimal
    gross_reward_to_risk: Decimal
    estimated_cost_inr: Decimal
    estimated_net_reward_inr: Decimal
    estimated_net_risk_inr: Decimal
    estimated_net_reward_to_risk: Decimal
    forecast_is_uncertain: bool = True
    stop_loss_is_guaranteed: bool = False


def estimate_trade_economics(plan: TradePlan, costs: TradingCosts) -> TradeEconomics:
    """Calculate risk at planned stop and target reward using exact decimal arithmetic."""
    entry = (plan.entry_low + plan.entry_high) / Decimal(2)
    if plan.side == "BUY":
        loss_per_unit = entry - plan.stop_price
        reward_per_unit = plan.target_price - entry
    else:
        loss_per_unit = plan.stop_price - entry
        reward_per_unit = entry - plan.target_price
    if loss_per_unit <= 0 or reward_per_unit <= 0:
        raise TradeEconomicsError("trade plan must have positive planned loss and target reward")
    planned_loss = loss_per_unit * plan.quantity
    gross_reward = reward_per_unit * plan.quantity
    net_reward = gross_reward - costs.total_inr
    net_risk = planned_loss + costs.total_inr
    return TradeEconomics(
        entry_reference=entry,
        planned_loss_inr=planned_loss,
        gross_reward_inr=gross_reward,
        gross_reward_to_risk=gross_reward / planned_loss,
        estimated_cost_inr=costs.total_inr,
        estimated_net_reward_inr=net_reward,
        estimated_net_risk_inr=net_risk,
        estimated_net_reward_to_risk=net_reward / net_risk,
    )


__all__ = ["TradeEconomics", "TradeEconomicsError", "TradingCosts", "estimate_trade_economics"]
