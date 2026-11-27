from decimal import Decimal

import pytest

from financial_brain.strategies.risk_scaled_intents import build_scale_plan


def test_e1_e2_are_distinct_and_combined_worst_case_stays_inside_cap():
    plan = build_scale_plan(
        parent_id="trade-42", e1_condition="breakout close", e2_condition="retest holds",
        max_account_risk=Decimal("1000"), e1_risk_per_unit=Decimal("120"),
        e2_risk_per_unit=Decimal("175"), e1_budget_fraction=Decimal("0.4"))
    e1, e2 = plan.intents
    assert (e1.stage, e2.stage) == ("E1", "E2")
    assert e1.intent_id != e2.intent_id
    assert e2.requires_filled_intent == e1.intent_id
    assert plan.worst_case_reserved_risk == e1.reserved_risk + e2.reserved_risk
    assert plan.worst_case_reserved_risk <= plan.max_account_risk


def test_zero_e1_capacity_does_not_let_e2_exceed_account_risk():
    plan = build_scale_plan(
        parent_id="trade-7", e1_condition="c1", e2_condition="c2",
        max_account_risk=Decimal("100"), e1_risk_per_unit=Decimal("101"),
        e2_risk_per_unit=Decimal("60"))
    assert plan.intents[0].quantity == 0
    assert plan.intents[1].quantity == 0  # E2 requires a filled E1.
    assert plan.worst_case_reserved_risk <= Decimal("100")


def test_rejects_duplicate_condition_or_invalid_risk_inputs():
    with pytest.raises(ValueError, match="distinct"):
        build_scale_plan(parent_id="p", e1_condition="same", e2_condition="same",
                         max_account_risk=Decimal("100"), e1_risk_per_unit=Decimal("5"),
                         e2_risk_per_unit=Decimal("5"))
    with pytest.raises(ValueError, match="positive"):
        build_scale_plan(parent_id="p", e1_condition="a", e2_condition="b",
                         max_account_risk=Decimal("100"), e1_risk_per_unit=Decimal("0"),
                         e2_risk_per_unit=Decimal("5"))
