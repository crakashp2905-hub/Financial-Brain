"""Entry, exit, precision, and paper-eligibility plan attached to a signal."""
from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from enum import StrEnum


class PlanError(ValueError):
    pass


class Comparison(StrEnum):
    GT = ">"
    GTE = ">="
    LT = "<"
    LTE = "<="
    EQ = "=="


@dataclass(frozen=True, slots=True)
class EntryCondition:
    feature: str
    operator: Comparison
    threshold: float

    def __post_init__(self) -> None:
        if not self.feature.strip() or not math.isfinite(self.threshold):
            raise PlanError("entry condition requires a feature and finite threshold")


@dataclass(frozen=True, slots=True)
class InstrumentPrecision:
    tick_size: Decimal
    quantity_step: Decimal
    minimum_quantity: Decimal

    def __post_init__(self) -> None:
        if min(self.tick_size, self.quantity_step, self.minimum_quantity) <= 0:
            raise PlanError("tick and quantity precision values must be positive")

    def quantize_quantity(self, quantity: Decimal) -> Decimal:
        if quantity < self.minimum_quantity:
            raise PlanError("quantity is below instrument minimum")
        steps = (quantity / self.quantity_step).to_integral_value(rounding=ROUND_DOWN)
        rounded = steps * self.quantity_step
        if rounded < self.minimum_quantity:
            raise PlanError("rounded quantity is below instrument minimum")
        return rounded

    def quantize_price(self, price: Decimal, *, rounding=ROUND_HALF_UP) -> Decimal:
        if price <= 0:
            raise PlanError("price must be positive")
        steps = (price / self.tick_size).to_integral_value(rounding=rounding)
        return steps * self.tick_size


@dataclass(frozen=True, slots=True)
class SignalPlan:
    signal_id: str
    entry_conditions: tuple[EntryCondition, ...]
    holding_window_sessions: int
    precision: InstrumentPrecision
    stop_loss_return: float | None
    take_profit_return: float | None
    invalidation_conditions: tuple[EntryCondition, ...]
    paper_execution_eligible: bool
    eligibility_reason: str | None = None

    def __post_init__(self) -> None:
        if not self.signal_id.strip() or self.holding_window_sessions < 1:
            raise PlanError("signal_id and positive holding window are required")
        for name, value in (("stop_loss_return", self.stop_loss_return),
                            ("take_profit_return", self.take_profit_return)):
            if value is not None and not math.isfinite(value):
                raise PlanError(f"{name} must be finite")
        if self.stop_loss_return is not None and self.stop_loss_return >= 0:
            raise PlanError("stop loss return must be negative")
        if self.take_profit_return is not None and self.take_profit_return <= 0:
            raise PlanError("take profit return must be positive")
        if self.paper_execution_eligible:
            if not self.entry_conditions:
                raise PlanError("eligible paper plan requires entry conditions")
            if self.eligibility_reason:
                raise PlanError("eligible paper plan cannot have a rejection reason")
        elif not self.eligibility_reason or not self.eligibility_reason.strip():
            raise PlanError("ineligible paper plan requires an explicit reason")
