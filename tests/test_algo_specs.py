"""Algorithm specification, clock, order, cost and abstention tests (ALG-012..022)."""

from __future__ import annotations

import pytest

from financial_brain.strategies.specs import (
    PARITY_LABEL,
    AlgorithmSpec,
    BarClock,
    CostModel,
    OrderSpec,
    ReferenceBehaviorFixture,
    SpecError,
    alpha_vs_cash,
    attach_cost_version,
    cash_baseline_returns,
    check_fixture,
    decision_inputs_ok,
    order_is_coverable,
)


def _spec(**over: object) -> AlgorithmSpec:
    kw: dict[str, object] = dict(
        algorithm_id="donchian_20_cash", source_ids=("TV-001",), family="trend_breakout", version="1",
        segment="CASH_EQUITY", timeframe="1D", clock=BarClock("NSE_CASH", signal_bar=10),
        parameters={"lookback": 20}, cost_model_version="cost-v1",
    )
    kw.update(over)
    return AlgorithmSpec(**kw)  # type: ignore[arg-type]


def test_a_valid_spec_hashes_deterministically() -> None:
    assert _spec().spec_sha256() == _spec().spec_sha256()
    assert _spec().spec_sha256() != _spec(parameters={"lookback": 10}).spec_sha256()  # ALG-019


def test_unversioned_or_unsourced_specs_fail() -> None:
    with pytest.raises(SpecError):
        _spec(version="")
    with pytest.raises(SpecError):
        _spec(source_ids=())


def test_cash_equity_cannot_be_shorted() -> None:  # ALG-015
    with pytest.raises(SpecError, match="cash equity"):
        _spec(short_allowed=True)
    assert _spec(segment="STOCK_FUTURES", short_allowed=True).short_allowed


def test_foreign_session_clocks_are_refused() -> None:  # ALG-054
    with pytest.raises(SpecError):
        _spec(parameters={"london_open": "08:00"})
    with pytest.raises(SpecError):
        _spec(parameters={"open_text": "09:30 ET"})


def test_parity_cannot_be_claimed_exactly() -> None:  # ALG-018
    assert _spec().parity == PARITY_LABEL
    with pytest.raises(SpecError):
        _spec(parity="EXACT_PINE_PARITY")


def test_signal_on_a_closed_bar_never_fills_at_its_own_open() -> None:  # ALG-013
    clock = BarClock("NSE_CASH", signal_bar=10)
    assert clock.earliest_fill_index() == 11


def test_order_combinations_are_validated() -> None:  # ALG-014
    assert OrderSpec(kind="STOP", side="BUY", quantity=5, stop_price=101.0).kind == "STOP"
    with pytest.raises(SpecError):
        OrderSpec(kind="STOP", side="BUY", quantity=5)
    with pytest.raises(SpecError):
        OrderSpec(kind="MARKET", side="SELL", quantity=5, limit_price=100.0)
    with pytest.raises(SpecError):
        OrderSpec(kind="LIMIT", side="SELL", quantity=0, limit_price=100.0)


def test_results_must_name_their_cost_model_version() -> None:  # ALG-016
    cost = CostModel(version="cost-v1", segment="CASH_EQUITY", fee_bps=3, spread_bps=5, impact_bps=2, stt_or_tax_bps=10)
    out = attach_cost_version({"net": 1.0}, cost)
    assert out["cost_model_version"] == "cost-v1" and len(out["cost_model_sha256"]) == 64
    with pytest.raises(SpecError):
        attach_cost_version({}, cost)
    assert cost.round_trip_bps() > 0


def test_fixture_checks_expected_signal_and_decision_indices() -> None:  # ALG-017
    fx = ReferenceBehaviorFixture("breakout", (1.0, 2.0, 3.0), (2,), (3,))
    assert check_fixture(fx, [2], [3])
    assert not check_fixture(fx, [2], [2])


def test_missing_or_nonfinite_inputs_abstain() -> None:  # ALG-021
    assert decision_inputs_ok([1.0, 2.0]) == "OK"
    for bad in ([None], [0.0], [float("nan")], [-1.0]):
        assert decision_inputs_ok(bad) == "ABSTAIN"


def test_cash_baseline_and_positive_drift_do_not_imply_alpha() -> None:  # ALG-022
    assert cash_baseline_returns(3) == [0.0, 0.0, 0.0]
    drifting = [0.001] * 100  # a positive-drift fixture
    assert alpha_vs_cash(drifting) > 0  # the arithmetic is right; it is not evidence of skill on its own


def test_coverable_orders_fail_closed() -> None:  # ALG-088 companion
    order = OrderSpec(kind="MARKET", side="BUY", quantity=10)
    assert order_is_coverable(order, available_quantity=10)
    assert not order_is_coverable(order, available_quantity=9)
