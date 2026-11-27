"""Risk-capped E1/E2 research intents with a shared worst-case loss budget."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR


@dataclass(frozen=True)
class ScaleIntent:
    intent_id: str
    parent_id: str
    stage: str
    condition: str
    quantity: int
    risk_per_unit: Decimal
    reserved_risk: Decimal
    requires_filled_intent: str | None = None


@dataclass(frozen=True)
class ScalePlan:
    parent_id: str
    max_account_risk: Decimal
    intents: tuple[ScaleIntent, ScaleIntent]

    @property
    def worst_case_reserved_risk(self) -> Decimal:
        return sum((intent.reserved_risk for intent in self.intents), Decimal("0"))


def _floor_units(risk: Decimal, unit_risk: Decimal) -> int:
    return int((risk / unit_risk).to_integral_value(rounding=ROUND_FLOOR))


def build_scale_plan(
    *,
    parent_id: str,
    e1_condition: str,
    e2_condition: str,
    max_account_risk: Decimal,
    e1_risk_per_unit: Decimal,
    e2_risk_per_unit: Decimal,
    e1_budget_fraction: Decimal = Decimal("0.5"),
) -> ScalePlan:
    """Size two separately triggered intents so their combined max loss stays capped.

    Per-unit risk must include stop distance and estimated costs. E1/E2 remain distinct
    conditional paper intents; zero quantity means that stage is not eligible to enter.
    """
    if not parent_id or not e1_condition or not e2_condition or e1_condition == e2_condition:
        raise ValueError("scale stages require a parent and distinct nonempty conditions")
    values = (max_account_risk, e1_risk_per_unit, e2_risk_per_unit, e1_budget_fraction)
    if any(not value.is_finite() for value in values):
        raise ValueError("risk inputs must be finite")
    if (max_account_risk <= 0 or e1_risk_per_unit <= 0 or e2_risk_per_unit <= 0
            or not Decimal("0") < e1_budget_fraction < Decimal("1")):
        raise ValueError("risk budget and unit risks must be positive; split must be between zero and one")
    first_budget = max_account_risk * e1_budget_fraction
    first_qty = _floor_units(first_budget, e1_risk_per_unit)
    first_reserved = e1_risk_per_unit * first_qty
    second_budget = max_account_risk - first_reserved
    second_qty = (_floor_units(second_budget, e2_risk_per_unit) if first_qty > 0 else 0)
    second_reserved = e2_risk_per_unit * second_qty
    plan = ScalePlan(parent_id, max_account_risk, (
        ScaleIntent(f"{parent_id}:E1", parent_id, "E1", e1_condition, first_qty,
                    e1_risk_per_unit, first_reserved),
        ScaleIntent(f"{parent_id}:E2", parent_id, "E2", e2_condition, second_qty,
                    e2_risk_per_unit, second_reserved, f"{parent_id}:E1"),
    ))
    if plan.worst_case_reserved_risk > max_account_risk:
        raise ArithmeticError("scale sizing exceeded the shared account risk cap")
    return plan
