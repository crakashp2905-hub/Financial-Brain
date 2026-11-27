import pytest

from financial_brain.risk.scenario_loss import (
    AdverseScenario, ScenarioLossError, worst_position_loss,
)


def test_position_risk_scales_to_size_and_adds_stress_scenario_fees():
    estimate = worst_position_loss(
        side="LONG", quantity=100, multiplier=2, entry_price=100,
        scenarios=(AdverseScenario("stop", 95, 40),
                   AdverseScenario("gap-through-stop", 90, 75)))
    assert estimate.worst_scenario_id == "gap-through-stop"
    assert estimate.gross_loss_inr == 2_000
    assert estimate.fees_inr == 75
    assert estimate.worst_net_loss_inr == 2_075


def test_short_risk_uses_adverse_upside_and_costs():
    estimate = worst_position_loss(
        side="SHORT", quantity=3, multiplier=25, entry_price=200,
        scenarios=(AdverseScenario("rally-gap", 220, 90),))
    assert estimate.worst_net_loss_inr == 1_590


@pytest.mark.parametrize("kwargs", [
    {"quantity": 0}, {"entry_price": 0}, {"multiplier": float("inf")},
])
def test_invalid_size_and_price_inputs_fail_closed(kwargs):
    values = dict(side="LONG", quantity=1, multiplier=1, entry_price=100,
                  scenarios=(AdverseScenario("shock", 90, 5),))
    values.update(kwargs)
    with pytest.raises(ScenarioLossError):
        worst_position_loss(**values)


def test_empty_duplicate_and_negative_fee_scenarios_are_refused():
    common = dict(side="LONG", quantity=1, multiplier=1, entry_price=100)
    with pytest.raises(ScenarioLossError, match="scenario"):
        worst_position_loss(**common, scenarios=())
    with pytest.raises(ScenarioLossError, match="scenario ids"):
        worst_position_loss(**common, scenarios=(AdverseScenario("x", 90, 1),
                                                   AdverseScenario("x", 80, 1)))
    with pytest.raises(ScenarioLossError, match="scenario ids"):
        worst_position_loss(**common, scenarios=(AdverseScenario("x", 90, -1),))
