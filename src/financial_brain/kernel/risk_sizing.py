"""Stop-distance and capital constrained paper position sizing."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_DOWN

from .trader_signal import TradePlan


class RiskSizingError(ValueError):
    """Sizing inputs do not describe feasible finite capital and market limits."""


def _value(name: str, raw: Decimal | str | int, *, positive: bool = False) -> Decimal:
    try:
        value = raw if isinstance(raw, Decimal) else Decimal(str(raw))
    except (InvalidOperation, ValueError) as exc:
        raise RiskSizingError(f"{name} must be decimal") from exc
    if not value.is_finite() or (value <= 0 if positive else value < 0):
        raise RiskSizingError(f"{name} must be finite and {'positive' if positive else 'non-negative'}")
    return value


@dataclass(frozen=True, slots=True)
class RiskSizingResult:
    eligible: bool
    quantity: Decimal
    entry_reference: Decimal
    planned_loss_inr: Decimal
    notional_inr: Decimal
    binding_limits: tuple[str, ...]
    reasons: tuple[str, ...]


def size_trade_plan(
    plan: TradePlan,
    *,
    risk_budget_inr: Decimal | str | int,
    available_capital_inr: Decimal | str | int,
    max_notional_inr: Decimal | str | int,
    depth_available_units: Decimal | str | int,
    max_depth_participation: Decimal | str | int,
    correlated_exposure_room_inr: Decimal | str | int,
) -> RiskSizingResult:
    """Size to the minimum of stop risk, capital, notional, depth and correlation room.

    This computes a paper quantity from supplied decision-time caps. It does not
    guarantee a fill, stop execution, broker margin availability, or portfolio safety.
    """
    risk = _value("risk_budget_inr", risk_budget_inr)
    capital = _value("available_capital_inr", available_capital_inr, positive=True)
    notional_cap = _value("max_notional_inr", max_notional_inr, positive=True)
    depth = _value("depth_available_units", depth_available_units, positive=True)
    participation = _value("max_depth_participation", max_depth_participation)
    correlated = _value("correlated_exposure_room_inr", correlated_exposure_room_inr)
    if participation > 1:
        raise RiskSizingError("max_depth_participation must be between 0 and 1")

    entry = (plan.entry_low + plan.entry_high) / Decimal(2)
    stop_distance = abs(entry - plan.stop_price)
    if stop_distance <= 0:
        raise RiskSizingError("entry-to-stop distance must be positive")
    quantum = Decimal(1).scaleb(-plan.unit_precision)
    if (plan.lot_size / quantum) != (plan.lot_size / quantum).to_integral_value():
        raise RiskSizingError("lot_size must be representable at unit_precision")

    caps = {
        "DESIRED_QUANTITY": plan.quantity,
        "STOP_RISK": risk / stop_distance,
        "CAPITAL": capital / entry,
        "NOTIONAL": notional_cap / entry,
        "DEPTH": depth * participation,
        "CORRELATED_EXPOSURE": correlated / entry,
    }
    limiting = min(caps.values())
    raw_lots = (limiting / plan.lot_size).to_integral_value(rounding=ROUND_DOWN)
    quantity = raw_lots * plan.lot_size
    reasons = () if quantity > 0 else ("NO_FEASIBLE_LOT_WITHIN_LIMITS",)
    binding = tuple(name for name, limit in caps.items() if limit == limiting)
    planned_loss = quantity * stop_distance
    notional = quantity * entry
    return RiskSizingResult(
        eligible=quantity > 0, quantity=quantity, entry_reference=entry,
        planned_loss_inr=planned_loss, notional_inr=notional,
        binding_limits=binding, reasons=reasons,
    )


__all__ = ["RiskSizingError", "RiskSizingResult", "size_trade_plan"]
