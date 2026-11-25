from datetime import datetime, timezone

import pytest

from financial_brain.portfolio.scenario_optimizer import (
    ScenarioCandidate,
    ScenarioKind,
    ScenarioOptimizationError,
    StressScenario,
    optimize_scenarios,
)

AS_OF = datetime(2026, 11, 25, 9, 30, tzinfo=timezone.utc)
HASH = "a" * 64


def _inputs():
    candidates = (
        ScenarioCandidate("cash-equity", 0.15, 0.50, 0.80, HASH),
        ScenarioCandidate("long-option", 0.30, 0.60, 0.50, "b" * 64),
    )
    scenarios = (
        StressScenario("sector-tech", ScenarioKind.SECTOR_SHOCK, AS_OF,
                       {"cash-equity": -0.30, "long-option": -0.50}, 1.0, 0.20, "c" * 64),
        StressScenario("index-crash", ScenarioKind.INDEX_CRASH, AS_OF,
                       {"cash-equity": -0.20, "long-option": -0.40}, 1.0, 0.15, "d" * 64),
        StressScenario("gamma-gap", ScenarioKind.OPTION_GAMMA_SHOCK, AS_OF,
                       {"cash-equity": -0.02, "long-option": -0.80}, 1.0, 0.10, "e" * 64),
        StressScenario("funding-squeeze", ScenarioKind.FUNDING_STRESS, AS_OF,
                       {"cash-equity": -0.10, "long-option": -0.10}, 2.0, 0.25, "f" * 64),
    )
    return candidates, scenarios


def test_optimizer_respects_all_joint_stress_and_funding_constraints():
    candidates, scenarios = _inputs()
    result = optimize_scenarios(candidates=candidates, scenarios=scenarios, as_of=AS_OF,
                                funding_capacity_fraction=0.40, cash_return_hurdle=0.02)
    assert sum(result.weights.values()) + result.cash_weight == pytest.approx(1.0)
    assert result.expected_excess_return_vs_cash >= 0
    assert result.stressed_funding_fraction <= 0.40 + 1e-9
    assert result.weights.get("long-option", 0.0) <= 0.125 + 1e-9
    for scenario in scenarios:
        assert result.scenario_returns[scenario.scenario_id] >= -scenario.maximum_loss_fraction - 1e-9
    assert not result.promotion_allowed
    assert result == optimize_scenarios(candidates=candidates, scenarios=scenarios, as_of=AS_OF,
                                        funding_capacity_fraction=0.40, cash_return_hurdle=0.02)


def test_candidate_below_cash_hurdle_keeps_whole_budget_in_cash():
    candidates, scenarios = _inputs()
    unprofitable = tuple(ScenarioCandidate(row.candidate_id, 0.01, row.funding_fraction,
                                           row.maximum_weight, row.evidence_sha256)
                         for row in candidates)
    result = optimize_scenarios(candidates=unprofitable, scenarios=scenarios, as_of=AS_OF,
                                funding_capacity_fraction=0.40, cash_return_hurdle=0.02)
    assert result.weights == {}
    assert result.cash_weight == 1.0
    assert result.expected_excess_return_vs_cash == 0.0


def test_required_scenario_classes_candidate_coverage_and_asof_must_be_complete():
    candidates, scenarios = _inputs()
    with pytest.raises(ScenarioOptimizationError, match="scenario types are missing"):
        optimize_scenarios(candidates=candidates, scenarios=scenarios[:-1], as_of=AS_OF,
                           funding_capacity_fraction=0.4, cash_return_hurdle=0.0)
    bad = (*scenarios[:-1], StressScenario("funding-squeeze", ScenarioKind.FUNDING_STRESS, AS_OF,
                                           {"cash-equity": -0.1}, 2.0, 0.25, HASH))
    with pytest.raises(ScenarioOptimizationError, match="exactly the frozen candidate set"):
        optimize_scenarios(candidates=candidates, scenarios=bad, as_of=AS_OF,
                           funding_capacity_fraction=0.4, cash_return_hurdle=0.0)
