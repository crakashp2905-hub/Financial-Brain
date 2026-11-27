import pytest

from financial_brain.risk.scenario_loss import (
    AdverseScenario, ScenarioLossError, stress_margin_depletion, worst_position_loss,
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


def test_gap_through_stop_loss_can_breach_maintenance_and_deplete_equity():
    loss = worst_position_loss(
        side="LONG", quantity=100, multiplier=1, entry_price=100,
        scenarios=(AdverseScenario("intended-stop", 95, 10),
                   AdverseScenario("gap-through-stop", 80, 20)),
    )
    outcomes = stress_margin_depletion(
        pre_stress_equity_inr=2_000,
        maintenance_margin_inr=1_000,
        stressed_losses=loss.scenario_losses_inr,
    )
    by_id = {item.scenario_id: item for item in outcomes}
    assert by_id["intended-stop"].post_stress_equity_inr == 1_490
    assert by_id["intended-stop"].maintenance_breach is False
    assert by_id["gap-through-stop"].post_stress_equity_inr == -20
    assert by_id["gap-through-stop"].maintenance_breach is True
    assert by_id["gap-through-stop"].margin_shortfall_inr == 1_020
    assert by_id["gap-through-stop"].depleted is True


def test_margin_stress_rejects_invalid_inputs():
    with pytest.raises(ScenarioLossError):
        stress_margin_depletion(pre_stress_equity_inr=-1, maintenance_margin_inr=0,
                                stressed_losses=(("gap", 1),))
    with pytest.raises(ScenarioLossError):
        stress_margin_depletion(pre_stress_equity_inr=100, maintenance_margin_inr=10,
                                stressed_losses=(("gap", -1),))
