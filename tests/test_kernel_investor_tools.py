from __future__ import annotations

from decimal import Decimal

import pytest

from financial_brain.kernel.investor_tools import (
    ExposureGuard,
    buy_and_hold_with_dividends,
    dca_simulate,
    factor_scores,
    flatten_all,
)


def test_factor_scores_are_none_without_enough_history() -> None:
    scores = factor_scores([100.0, 101.0])
    assert scores["momentum"] is None and scores["low_risk"] is None
    assert scores["reversal"] == pytest.approx(-(101.0 / 100.0 - 1.0))


def test_momentum_skips_the_latest_month() -> None:
    prices = [100.0] * 13
    prices[-2] = 120.0
    prices[-1] = 1.0  # latest month must not affect momentum
    assert factor_scores(prices)["momentum"] == pytest.approx(0.2)


def test_buy_and_hold_taxes_dividends_only() -> None:
    result = buy_and_hold_with_dividends(
        units=Decimal("10"), final_price=Decimal("120"), dividends_per_unit=[Decimal("1"), Decimal("2")], tax_rate=Decimal("0.2")
    )
    assert result["dividends_gross"] == Decimal("30")
    assert result["dividends_net"] == Decimal("24")
    assert result["total_net"] == Decimal("1224")


def test_dca_buys_more_units_when_prices_fall() -> None:
    result = dca_simulate(Decimal("100"), [Decimal("10"), Decimal("20")])
    assert result["units"] == Decimal("15")
    assert result["invested"] == Decimal("200")
    assert result["final_value"] == Decimal("300")


def test_exposure_guard_latches_until_a_human_resets_it() -> None:
    guard = ExposureGuard(limit=Decimal("100"))
    assert guard.update(Decimal("50")) is True
    assert guard.update(Decimal("150")) is False
    assert guard.update(Decimal("10")) is False  # latched, does not recover on its own
    with pytest.raises(ValueError):
        guard.reset_by_human("")
    guard.reset_by_human("ops-lead")
    assert guard.update(Decimal("10")) is True


def test_flatten_touches_only_owned_positions() -> None:
    orders = flatten_all({"ABC": 10, "XYZ": -5, "OTHER": 3}, owned={"ABC", "XYZ"})
    assert {"symbol": "ABC", "side": "SELL", "quantity": 10} in orders
    assert {"symbol": "XYZ", "side": "BUY", "quantity": 5} in orders
    assert all(o["symbol"] != "OTHER" for o in orders)
