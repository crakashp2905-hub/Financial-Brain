import pytest

from financial_brain.forward_edge.queue_fill_scenarios import (
    QueueFillError,
    queue_fill_scenarios,
)


def _intention(**changes):
    row = {
        "intention_id": "intent-1", "order_quantity": 10,
        "queue_ahead_min": 2, "queue_ahead_max": 6,
        "traded_through_quantity": 8,
        "gross_pnl_full_fill_inr": 100.0, "cost_full_fill_inr": 20.0,
    }
    row.update(changes)
    return row


def test_alternative_fill_scenarios_and_queue_bounds_are_explicit():
    report = queue_fill_scenarios([_intention()], nav_inr=10_000)
    scenarios = report["scenario_totals"]
    assert set(scenarios) == {
        "NO_FILL", "QUEUE_BEST_CASE", "QUEUE_MIDPOINT", "QUEUE_WORST_CASE", "FULL_FILL"
    }
    assert scenarios["QUEUE_BEST_CASE"]["filled_quantity"] == 6
    assert scenarios["QUEUE_MIDPOINT"]["filled_quantity"] == 4
    assert scenarios["QUEUE_WORST_CASE"]["filled_quantity"] == 2
    assert scenarios["NO_FILL"]["net_pnl_inr"] == 0
    assert scenarios["FULL_FILL"]["net_pnl_inr"] == 80
    assert report["queue_position_net_pnl_bounds_inr"] == {
        "lower": 16.0, "lower_scenario": "QUEUE_WORST_CASE",
        "upper": 48.0, "upper_scenario": "QUEUE_BEST_CASE",
    }
    assert report["evidence_class"] == "QUEUE_POSITION_SCENARIO"


def test_economic_best_and_worst_bounds_do_not_assume_more_fills_are_better():
    report = queue_fill_scenarios(
        [_intention(gross_pnl_full_fill_inr=-100.0)], nav_inr=10_000
    )
    bounds = report["queue_position_net_pnl_bounds_inr"]
    assert bounds == {
        "lower": -72.0, "lower_scenario": "QUEUE_BEST_CASE",
        "upper": -24.0, "upper_scenario": "QUEUE_WORST_CASE",
    }
    assert report["best_case_net_pnl_inr"] == 0
    assert report["worst_case_net_pnl_inr"] == -120


def test_each_intention_exposes_fill_fraction_and_population_hash_is_order_independent():
    first = _intention()
    second = _intention(intention_id="intent-2", queue_ahead_min=0, queue_ahead_max=2)
    a = queue_fill_scenarios([first, second], nav_inr=10_000)
    b = queue_fill_scenarios([second, first], nav_inr=10_000)
    assert a["intention_population_sha256"] == b["intention_population_sha256"]
    row = next(x for x in a["intentions"] if x["intention_id"] == "intent-1")
    assert row["scenarios"]["QUEUE_BEST_CASE"]["fill_fraction"] == pytest.approx(0.6)
    assert row["scenarios"]["QUEUE_WORST_CASE"]["fill_fraction"] == pytest.approx(0.2)


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"order_quantity": 0}, "invalid queue bounds"),
        ({"queue_ahead_min": -1}, "invalid queue bounds"),
        ({"queue_ahead_max": 1}, "invalid queue bounds"),
        ({"traded_through_quantity": 1.5}, "quantities must be integers"),
        ({"cost_full_fill_inr": -1}, "invalid gross P&L or cost"),
        ({"gross_pnl_full_fill_inr": float("nan")}, "invalid gross P&L or cost"),
    ],
)
def test_malformed_queue_and_economic_inputs_fail_closed(changes, message):
    with pytest.raises(QueueFillError, match=message):
        queue_fill_scenarios([_intention(**changes)], nav_inr=10_000)


def test_duplicate_ids_and_empty_populations_are_rejected():
    with pytest.raises(QueueFillError, match="unique"):
        queue_fill_scenarios([_intention(), _intention()], nav_inr=10_000)
    with pytest.raises(QueueFillError, match="at least one"):
        queue_fill_scenarios([], nav_inr=10_000)
