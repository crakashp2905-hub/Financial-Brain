from __future__ import annotations

import dataclasses
from decimal import Decimal
from pathlib import Path

import pytest

from financial_brain.kernel.risk import (
    ALLOW,
    DENY,
    Latch,
    MarketState,
    OrderRequest,
    Policy,
    RiskError,
    apply_breaches,
    evaluate,
)

POLICY = Policy(
    max_order_notional=Decimal("50000"),
    max_position_units=500,
    max_gross_exposure=Decimal("200000"),
    max_drawdown=Decimal("0.10"),
    max_daily_loss=Decimal("5000"),
    max_stale_seconds=60,
)


def _state(**kw: object) -> MarketState:
    base = dict(
        equity=Decimal("100000"), peak_equity=Decimal("100000"), day_pnl=Decimal("0"),
        gross_exposure=Decimal("0"), positions={}, pending={}, data_age_seconds=5, data_valid=True,
    )
    base.update(kw)
    return MarketState(**base)  # type: ignore[arg-type]


def _buy(qty: int = 100, price: str = "100") -> OrderRequest:
    return OrderRequest("NSE:ABC", "BUY", qty, Decimal(price))


def test_clean_order_is_allowed() -> None:
    assert evaluate(POLICY, _buy(), _state(), Latch())["decision"] == ALLOW


def test_order_notional_cap_denies_with_reason() -> None:
    out = evaluate(POLICY, _buy(qty=600, price="100"), _state(), Latch())
    assert out["decision"] == DENY and "ORDER_NOTIONAL_CAP" in out["reasons"]


def test_pending_orders_count_toward_position_cap() -> None:
    state = _state(positions={"NSE:ABC": 300}, pending={"NSE:ABC": 150})
    out = evaluate(POLICY, _buy(qty=100, price="10"), state, Latch())
    assert "POSITION_CAP" in out["reasons"]


def test_concurrent_orders_cannot_jointly_exceed_exposure() -> None:
    # Two orders each fit alone; with the first counted as gross exposure the second must be denied.
    wide = Policy(
        max_order_notional=Decimal("200000"), max_position_units=1000,
        max_gross_exposure=Decimal("200000"), max_drawdown=Decimal("0.10"),
        max_daily_loss=Decimal("5000"), max_stale_seconds=60,
    )
    first = evaluate(wide, _buy(qty=400, price="400"), _state(), Latch())
    assert first["decision"] == ALLOW  # 160000 notional, under 200000 gross
    second_state = _state(gross_exposure=Decimal("160000"))  # the first order, now pending
    second = evaluate(wide, _buy(qty=400, price="400"), second_state, Latch())
    assert "GROSS_EXPOSURE_CAP" in second["reasons"]


def test_stale_or_invalid_data_blocks_entries() -> None:
    assert "STALE_DATA" in evaluate(POLICY, _buy(), _state(data_age_seconds=120), Latch())["reasons"]
    assert "INVALID_DATA" in evaluate(POLICY, _buy(), _state(data_valid=False), Latch())["reasons"]


def test_drawdown_latches_and_survives_restart(tmp_path: Path) -> None:
    latch = Latch()
    state = _state(equity=Decimal("85000"))
    apply_breaches(POLICY, state, latch)
    assert latch.tripped == ["DRAWDOWN"]
    path = tmp_path / "risk-latch.json"
    latch.save(path)
    restored = Latch.load(path)
    out = evaluate(POLICY, _buy(), _state(), restored)  # healthy state, but latched
    assert out["decision"] == DENY and out["reasons"][0].startswith("LATCHED:")


def test_agent_cannot_reset_a_latch() -> None:
    latch = Latch(tripped=["DAILY_LOSS"])
    with pytest.raises(RiskError):
        latch.reset(actor="agent:assistant", reason="looks fine")
    latch.reset(actor="owner", reason="reviewed loss")
    assert latch.tripped == []


def test_daily_loss_breach_latches() -> None:
    latch = Latch()
    apply_breaches(POLICY, _state(day_pnl=Decimal("-6000")), latch)
    assert "DAILY_LOSS" in latch.tripped


def test_policy_cannot_be_loosened_in_place() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        POLICY.max_order_notional = Decimal("1")  # type: ignore[misc]


def test_invalid_policy_rejected() -> None:
    with pytest.raises(RiskError):
        Policy(Decimal("0"), 1, Decimal("1"), Decimal("0.1"), Decimal("1"), 1)
    with pytest.raises(RiskError):
        Policy(Decimal("1"), 1, Decimal("1"), Decimal("1.5"), Decimal("1"), 1)
