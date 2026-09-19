"""Square-root market impact: cost grows with order size relative to liquidity."""
from __future__ import annotations

import pytest

from financial_brain.costs.india import CostModel

C = CostModel()


def test_impact_scales_with_the_square_root_of_participation():
    small = C.sqrt_impact(order_value=1e6, adv_value=1e8, daily_vol=0.02)
    big = C.sqrt_impact(order_value=4e6, adv_value=1e8, daily_vol=0.02)
    assert small == pytest.approx(0.02 * 0.1)          # 1% of ADV -> 0.2%
    assert big == pytest.approx(2 * small), "4x the order, 2x the impact"


def test_volatile_and_illiquid_names_cost_more():
    base = C.sqrt_impact(order_value=1e6, adv_value=1e8, daily_vol=0.02)
    assert C.sqrt_impact(order_value=1e6, adv_value=1e8, daily_vol=0.04) == pytest.approx(2 * base)
    assert C.sqrt_impact(order_value=1e6, adv_value=2.5e7, daily_vol=0.02) == pytest.approx(2 * base)


def test_sized_round_trip_adds_impact_to_statutory_costs():
    rt = C.round_trip_sized(order_value=1e6, adv_value=1e8, daily_vol=0.02)
    assert rt["impact"] == pytest.approx(2 * 0.002)
    assert rt["fees"] > 0.002, "STT alone is 0.1% each way on delivery"
    assert rt["total"] == pytest.approx(rt["fees"] + rt["impact"])


def test_no_liquidity_means_no_trade():
    assert C.sqrt_impact(order_value=1e6, adv_value=0, daily_vol=0.02) == float("inf")
