import pytest

from financial_brain.forward_edge.stress import (
    ExecutionStressScenario,
    StressError,
    StressTrade,
    evaluate_execution_stress,
)


def test_cost_fill_and_outage_stresses_include_missed_fill_effects() -> None:
    trades = (StressTrade("a", 100_000, 2_000, {"fees": 100, "impact": 200}, False),
              StressTrade("b", 50_000, -500, {"fees": 80, "impact": 120}, True))
    scenarios = (
        ExecutionStressScenario("BASELINE", 1, 0, 1, 1),
        ExecutionStressScenario("COST_2X", 2, 0, 1, 1),
        ExecutionStressScenario("WORSE_FILLS_OUTAGE", 1.5, 50, 0.8, 0),
    )
    report = evaluate_execution_stress(trades, scenarios, starting_capital=100_000)
    assert report.baseline_net_pnl == 1_000
    cost_stress = report.scenarios[1]
    assert cost_stress.execution_costs == 1_000
    assert cost_stress.net_pnl == 500
    stressed = report.scenarios[2]
    assert stressed.filled_fraction == pytest.approx(0.8 * 100_000 / 150_000)
    assert stressed.missed_fill_count == 2
    assert stressed.gross_pnl == 1_600
    assert stressed.execution_costs > report.scenarios[0].execution_costs
    assert stressed.foregone_counterfactual_net_pnl != 0


def test_stress_requires_explicit_baseline_and_valid_assumptions() -> None:
    trade = StressTrade("a", 10_000, 100, {"fee": 5}, False)
    with pytest.raises(StressError, match="BASELINE"):
        evaluate_execution_stress((trade,), (ExecutionStressScenario("2X", 2, 0, 1, 1),),
                                  starting_capital=10_000)
    with pytest.raises(StressError, match="at least one"):
        ExecutionStressScenario("bad", 0.9, 0, 1, 1)


def test_zero_fill_is_explicit_and_costs_do_not_turn_into_fictitious_profit() -> None:
    trade = StressTrade("a", 10_000, 100, {"fee": 5}, True)
    report = evaluate_execution_stress(
        (trade,),
        (ExecutionStressScenario("BASELINE", 1, 0, 1, 1),
         ExecutionStressScenario("OUTAGE", 1, 0, 1, 0)),
        starting_capital=10_000)
    outage = report.scenarios[1]
    assert outage.filled_fraction == 0
    assert outage.gross_pnl == 0
    assert outage.execution_costs == 0
    assert outage.net_pnl == 0
    assert outage.foregone_counterfactual_net_pnl == 95
