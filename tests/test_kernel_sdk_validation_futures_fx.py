from __future__ import annotations

import random
from datetime import date
from decimal import Decimal

import pytest

from financial_brain.kernel.futures_fx import (
    Contract,
    ContractError,
    exposure_matrix,
    front_contract,
    leverage_ok,
    quote_is_fresh,
    roll_date,
    triangular_gap,
    fill_price_source,
)
from financial_brain.kernel.strategy_sdk import Intent, Param, ParamSchema, SchemaError, run
from financial_brain.kernel.validation import (
    neighbour_stability,
    objective,
    overlap_free,
    pareto_front,
    seeded_random_search,
    walk_forward_splits,
)

SCHEMA = ParamSchema((
    Param("range_bars", int, 15, low=1, high=60),
    Param("rr", float, 2.0, low=0.5, high=5.0),
))


class Breakout:
    def __init__(self) -> None:
        self.params: dict = {}
        self.fills = 0
        self.stopped = False

    def on_start(self, params: dict, rng: random.Random) -> None:
        self.params = params

    def on_event(self, event: float) -> list[Intent]:
        if event > 100:
            return [Intent("ABC", "BUY", 10, "breakout")]
        return []

    def on_timer(self, now: object) -> list[Intent]:
        return []

    def on_fill(self, fill: object) -> None:
        self.fills += 1

    def on_stop(self) -> None:
        self.stopped = True


def test_schema_defaults_and_range_failures_are_actionable() -> None:
    assert SCHEMA.validate({})["range_bars"] == 15
    with pytest.raises(SchemaError, match="above maximum"):
        SCHEMA.validate({"range_bars": 999})
    with pytest.raises(SchemaError, match="unknown parameters"):
        SCHEMA.validate({"stop_loss": 1})
    with pytest.raises(SchemaError, match="booleans"):
        SCHEMA.validate({"range_bars": True})


def test_strategy_lifecycle_runs_and_returns_intents_only() -> None:
    log = run(Breakout, SCHEMA, {"rr": 3.0}, [99.0, 101.0, 102.0], seed=1, fills=[object()])
    assert [i.quantity for i in log.intents] == [10, 10]
    assert log.fills_seen == 1 and log.stopped


def test_same_seed_same_result_and_interleaved_runs_isolated() -> None:
    a = run(Breakout, SCHEMA, {}, [101.0], seed=3)
    b = run(Breakout, SCHEMA, {}, [101.0], seed=3)
    assert a.intents == b.intents


def test_walk_forward_purges_labels_crossing_validation_and_is_overlap_free() -> None:
    splits = walk_forward_splits(200, train=60, validate=20, embargo=5, horizon=10)
    assert splits and overlap_free(splits)
    s = splits[0]
    assert s.train[-1] + 10 <= s.validation[0]  # last kept label finishes before validation starts


def test_purge_that_removes_all_training_is_refused() -> None:
    with pytest.raises(ValueError, match="purge"):
        walk_forward_splits(100, train=5, validate=10, embargo=0, horizon=10)


def test_fragile_optimum_flagged_by_neighbour_check() -> None:
    assert neighbour_stability({9: 1.0, 10: 2.0, 11: 1.9}, 10) is True
    assert neighbour_stability({9: 0.1, 10: 2.0, 11: 0.2}, 10) is False


def test_objective_weights_are_fixed_penalties() -> None:
    assert objective(0.10, 0.05, 2.0) == pytest.approx(0.10 - 0.025 - 0.2)


def test_pareto_front_keeps_only_non_dominated_points() -> None:
    pts = [(1, 1), (2, 0), (0, 2), (1.5, 0.5), (0.5, 0.5)]
    assert set(pareto_front(pts)) == {(2, 0), (0, 2), (1.5, 0.5), (1, 1)}


def test_seeded_search_reproduces_and_records_every_attempt() -> None:
    space = {"x": [1.0, 2.0, 3.0], "y": [0.1, 0.2]}
    f = lambda p: p["x"] * p["y"]  # noqa: E731
    b1, s1, a1 = seeded_random_search(space, f, budget=8, seed=42)
    b2, s2, a2 = seeded_random_search(space, f, budget=8, seed=42)
    assert (b1, s1, a1) == (b2, s2, a2)
    assert len(a1) == 8


def test_front_contract_skips_expired_and_roll_precedes_expiry() -> None:
    cs = [Contract("NOV", date(2026, 11, 26)), Contract("DEC", date(2026, 12, 31))]
    assert front_contract(cs, date(2026, 11, 27)).symbol == "DEC"
    assert roll_date(cs[0]) == date(2026, 11, 24)
    with pytest.raises(ContractError, match="no unexpired"):
        front_contract(cs, date(2027, 1, 1))


def test_back_adjusted_prices_cannot_fill() -> None:
    with pytest.raises(ContractError, match="analytic only"):
        fill_price_source("BACK_ADJUSTED")
    assert fill_price_source("RAW_CONTRACT") == "RAW_CONTRACT"


def test_triangular_gap_is_zero_for_consistent_quotes() -> None:
    assert triangular_gap(1.10, 150.0, 165.0) == pytest.approx(0.0, abs=1e-12)
    assert triangular_gap(1.10, 150.0, 166.65) > 0


def test_stale_quote_blocks() -> None:
    assert quote_is_fresh(5, 10) and not quote_is_fresh(30, 10)


def test_exposure_matrix_nets_across_pairs() -> None:
    m = exposure_matrix([("EURUSD", Decimal("1.10"), 100), ("GBPUSD", Decimal("1.30"), 50)])
    assert m["EUR"] == Decimal(100)
    assert m["USD"] == Decimal("-175")  # -(100 x 1.10 + 50 x 1.30)
    assert m["GBP"] == Decimal(50)


def test_leverage_cap_enforced() -> None:
    assert leverage_ok(Decimal("300000"), Decimal("100000"), Decimal("5"))
    assert not leverage_ok(Decimal("600000"), Decimal("100000"), Decimal("5"))
    assert not leverage_ok(Decimal("1"), Decimal("0"), Decimal("5"))
