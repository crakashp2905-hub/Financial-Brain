"""Stop-distance and capital constrained paper position sizing."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_DOWN

from .trader_signal import TradePlan
from .units import BasisPoints, INRNotional, Price, Quantity, UnitError, WholeUnits


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

    @property
    def quantity_units(self) -> Quantity:
        """Typed executable quantity; callers must not infer currency from a bare Decimal."""
        return Quantity(self.quantity)

    @property
    def entry_price_inr(self) -> Price:
        """INR entry reference used by the sizing calculation."""
        return Price(self.entry_reference, "INR")

    @property
    def planned_loss(self) -> INRNotional:
        """Planned stop loss in INR, explicitly distinguished from a price or quantity."""
        return INRNotional(self.planned_loss_inr)

    @property
    def position_notional(self) -> INRNotional:
        """Sized position exposure in INR."""
        return INRNotional(self.notional_inr)


def size_trade_plan(
    plan: TradePlan,
    *,
    risk_budget_inr: Decimal | str | int | INRNotional,
    available_capital_inr: Decimal | str | int | INRNotional,
    max_notional_inr: Decimal | str | int | INRNotional,
    depth_available_units: Decimal | str | int | WholeUnits,
    max_depth_participation: Decimal | str | int | BasisPoints,
    correlated_exposure_room_inr: Decimal | str | int | INRNotional,
) -> RiskSizingResult:
    """Size to the minimum of stop risk, capital, notional, depth and correlation room.

    This computes a paper quantity from supplied decision-time caps. It does not
    guarantee a fill, stop execution, broker margin availability, or portfolio safety.
    """
    try:
        risk_input = risk_budget_inr.amount if isinstance(risk_budget_inr, INRNotional) else risk_budget_inr
        capital_input = available_capital_inr.amount if isinstance(available_capital_inr, INRNotional) else available_capital_inr
        notional_input = max_notional_inr.amount if isinstance(max_notional_inr, INRNotional) else max_notional_inr
        depth_input = depth_available_units.units if isinstance(depth_available_units, WholeUnits) else depth_available_units
        participation_input = (
            max_depth_participation.as_rate()
            if isinstance(max_depth_participation, BasisPoints) else max_depth_participation
        )
        correlated_input = (
            correlated_exposure_room_inr.amount
            if isinstance(correlated_exposure_room_inr, INRNotional)
            else correlated_exposure_room_inr
        )
    except UnitError as exc:
        raise RiskSizingError(str(exc)) from exc
    risk = _value("risk_budget_inr", risk_input)
    capital = _value("available_capital_inr", capital_input, positive=True)
    notional_cap = _value("max_notional_inr", notional_input, positive=True)
    depth = _value("depth_available_units", depth_input, positive=True)
    participation = _value("max_depth_participation", participation_input)
    correlated = _value("correlated_exposure_room_inr", correlated_input)
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
