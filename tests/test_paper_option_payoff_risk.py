import pytest

from financial_brain.paper.options import OptionPaperError
from financial_brain.paper.option_payoff_risk import (
    OptionBuyFill,
    long_option_capital_at_risk,
)


def test_partial_fills_are_loss_bounded_and_open_quantity_stays_reserved():
    result = long_option_capital_at_risk(
        requested_contracts=30, lot_size=10, limit_premium=100,
        fills=(OptionBuyFill(10, 98),), cancelled_contracts=10,
    )
    assert result["filled_contracts"] == 10
    assert result["cancelled_contracts"] == 10
    assert result["open_contracts"] == 10
    assert result["maximum_loss_filled_position_inr"] > 980
    assert result["unfilled_order_reserve_inr"] > 1000
    assert result["total_capital_at_risk_or_reserved_inr"] == pytest.approx(
        result["maximum_loss_filled_position_inr"]
        + result["unfilled_order_reserve_inr"])


def test_full_cancellation_releases_all_unfilled_risk_reserve():
    result = long_option_capital_at_risk(
        requested_contracts=20, lot_size=10, limit_premium=50,
        fills=(), cancelled_contracts=20,
    )
    assert result["maximum_loss_filled_position_inr"] == 0
    assert result["unfilled_order_reserve_inr"] == 0
    assert result["total_capital_at_risk_or_reserved_inr"] == 0


@pytest.mark.parametrize("kwargs", [
    {"fills": (OptionBuyFill(30, 100),), "cancelled_contracts": 10},
    {"fills": (OptionBuyFill(10, 101),), "cancelled_contracts": 0},
    {"fills": (OptionBuyFill(7, 90),), "cancelled_contracts": 0},
])
def test_impossible_overfill_price_and_fractional_lot_are_rejected(kwargs):
    values = {"requested_contracts": 20, "lot_size": 10, "limit_premium": 100,
              "fills": (), "cancelled_contracts": 0}
    values.update(kwargs)
    with pytest.raises(OptionPaperError):
        long_option_capital_at_risk(**values)
