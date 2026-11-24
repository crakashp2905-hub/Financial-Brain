import pytest

from financial_brain.forward_edge.disaster_stress import (
    DisasterKind,
    DisasterScenario,
    DisasterStressError,
    StressPosition,
    evaluate_disaster_stress,
)


def _scenarios():
    return (
        DisasterScenario("gap", DisasterKind.GAP, {"*": -0.10},
                         exit_fill_fraction=0.5),
        DisasterScenario("limit-down", DisasterKind.LIMIT_DOWN, {"*": -0.20},
                         exit_fill_fraction=0.25),
        DisasterScenario("spread", DisasterKind.SPREAD_BLOWOUT, {"*": 0.0},
                         spread_blowout_bps=500),
        DisasterScenario("expiry", DisasterKind.EXPIRY_SHOCK, {"*": -0.05},
                         expiry_extra_loss={"NIFTY-OPT": 12_000}),
        DisasterScenario("partition", DisasterKind.NETWORK_PARTITION, {"*": -0.02},
                         exit_fill_fraction=0, network_partition=True),
    )


def test_disaster_scenarios_bound_loss_and_report_execution_and_expiry_effects():
    report = evaluate_disaster_stress(
        positions=(StressPosition("NIFTY-OPT", 10, 100, 50),),
        scenarios=_scenarios(), starting_equity=100_000, max_loss_fraction=0.10)
    by_id = {row.scenario_id: row for row in report.results}
    assert by_id["gap"].mark_pnl == -5_000
    assert by_id["gap"].unfilled_exit_exposure == 25_000
    assert by_id["spread"].execution_cost == 2_500
    assert by_id["expiry"].expiry_loss == 12_000
    assert not by_id["partition"].new_entries_allowed
    assert by_id["partition"].requires_reconciliation
    assert report.breaches_loss_limit
    assert len(report.report_hash) == 64


def test_disaster_suite_requires_each_scenario_class_and_complete_shock_coverage():
    positions = (StressPosition("A", 1, 100),)
    scenarios = _scenarios()
    with pytest.raises(DisasterStressError, match="kinds missing"):
        evaluate_disaster_stress(positions=positions, scenarios=scenarios[:-1],
                                 starting_equity=10_000, max_loss_fraction=0.1)
    missing = DisasterScenario("partition", DisasterKind.NETWORK_PARTITION, {},
                               network_partition=True)
    with pytest.raises(DisasterStressError, match="no price shock"):
        evaluate_disaster_stress(positions=positions,
                                 scenarios=(*scenarios[:-1], missing),
                                 starting_equity=10_000, max_loss_fraction=0.1)


def test_network_partition_cannot_be_declared_without_disabling_entries():
    with pytest.raises(DisasterStressError, match="activate the outage"):
        DisasterScenario("outage", DisasterKind.NETWORK_PARTITION, {"*": -0.1})
