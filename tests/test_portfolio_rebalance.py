import pytest

from financial_brain.portfolio.rebalance import RebalanceError, plan_rebalance


def test_rebalance_applies_cost_only_outside_band_and_charges_fixed_fee_once():
    plan = plan_rebalance(
        {"A": 0.10, "B": 0.20, "C": 0.0},
        {"A": 0.15, "B": 0.18, "C": 0.03},
        100_000,
        no_trade_band_weights={"A": 0.02, "B": 0.02, "C": 0.01},
        proportional_cost_rates={"A": 0.001, "B": 0.001, "C": 0.002},
        fixed_charge_inr_per_order={"A": 10, "B": 10, "C": 50},
        expected_improvement_inr={"A": 100, "B": 100, "C": 40},
    )

    assert plan.status == "PAPER_REBALANCE_PROPOSED"
    assert plan.proposed_weights == {"A": 0.15, "B": 0.20, "C": 0.0}
    assert plan.turnover_inr == pytest.approx(5_000)
    assert plan.total_cost_inr == pytest.approx(15)
    assert plan.expected_improvement_inr == pytest.approx(100)
    assert plan.expected_net_improvement_inr == pytest.approx(85)
    a, b, c = plan.lines
    assert a.proportional_cost_inr == pytest.approx(5)
    assert a.fixed_charge_inr == 10
    assert a.disposition == "PAPER_TRADE_PROPOSED"
    assert b.disposition == "WITHIN_NO_TRADE_BAND"
    assert b.trade_notional_inr == 0
    assert c.disposition == "REJECTED_COST_EXCEEDS_BENEFIT"
    assert c.total_cost_inr == pytest.approx(56)
    assert c.expected_net_improvement_inr == pytest.approx(-16)


def test_fixed_charge_can_make_small_order_unprofitable_and_full_costs_are_visible():
    plan = plan_rebalance(
        {"A": 0.0}, {"A": 0.01}, 10_000,
        no_trade_band_weights={"A": 0.001},
        proportional_cost_rates={"A": 0.001},
        fixed_charge_inr_per_order={"A": 25},
        expected_improvement_inr={"A": 20},
    )
    line = plan.lines[0]
    assert plan.status == "NO_ACTION"
    assert plan.turnover_inr == 0
    assert plan.total_cost_inr == 0
    assert plan.proposed_weights == {"A": 0.0}
    assert line.total_cost_inr == pytest.approx(25.1)
    assert line.expected_net_improvement_inr == pytest.approx(-5.1)


def test_rebalance_refuses_incomplete_cost_inputs_and_bad_values():
    kwargs = dict(
        current_weights={"A": 0.0, "B": 0.1}, target_weights={"A": 0.1, "B": 0.0},
        nav_inr=100_000, no_trade_band_weights={"A": 0.01, "B": 0.01},
        proportional_cost_rates={"A": 0.001, "B": 0.001},
        fixed_charge_inr_per_order={"A": 10, "B": 10},
        expected_improvement_inr={"A": 100, "B": 100},
    )
    with pytest.raises(RebalanceError, match="exactly cover"):
        plan_rebalance(**{**kwargs, "fixed_charge_inr_per_order": {"A": 10}})
    with pytest.raises(RebalanceError, match="positive"):
        plan_rebalance(**{**kwargs, "nav_inr": 0})
    with pytest.raises(RebalanceError, match="cost rates"):
        plan_rebalance(**{**kwargs, "proportional_cost_rates": {"A": -0.1, "B": 0.001}})
