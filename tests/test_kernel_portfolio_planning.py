from __future__ import annotations

from decimal import Decimal

import pytest

from financial_brain.kernel.portfolio_planning import (
    apply_shocks,
    cash_buffer_ok,
    get_profile,
    rebalance_preview,
    scorecard,
)


def test_cash_buffer_must_cover_margin_costs_and_buffer() -> None:
    assert cash_buffer_ok(Decimal("12000"), Decimal("10000"), Decimal("500"), Decimal("0.10")) is True
    assert cash_buffer_ok(Decimal("11000"), Decimal("10000"), Decimal("500"), Decimal("0.10")) is False


def test_scorecard_reports_missing_factors_instead_of_zeroing_them() -> None:
    value, missing = scorecard({"value": 1.0, "quality": None}, {"value": 1.0, "quality": 1.0})
    assert value == pytest.approx(1.0)
    assert missing == ["quality"]
    none_value, none_missing = scorecard({}, {"value": 1.0})
    assert none_value is None and none_missing == ["value"]


def test_stress_shocks_apply_to_weights() -> None:
    assert apply_shocks({"EQ": 0.6, "BOND": 0.4}, {"EQ": -0.20, "BOND": 0.05}) == pytest.approx(-0.10)
    assert apply_shocks({"EQ": 1.0}, {}) == 0.0


def test_rebalance_preview_moves_units_to_targets() -> None:
    trades = rebalance_preview({"A": 10}, {"A": 0.5, "B": 0.5}, {"A": 100.0, "B": 50.0}, equity=10000.0)
    assert trades == {"A": 40, "B": 100}  # A: 50 target - 10 held; B: 100 target - 0 held


def test_risk_profiles_are_fixed_and_unknown_is_refused() -> None:
    assert get_profile("intraday")["max_drawdown"] == Decimal("0.03")
    with pytest.raises(KeyError):
        get_profile("hedgefund")
