from __future__ import annotations

import pytest

from financial_brain.research.cross_asset import (
    Wrapper,
    bs_call,
    route_exposure,
    unfilled_leg_loss,
    vertical_scenarios,
)


def test_bs_call_intrinsic_at_expiry():
    assert bs_call(110, 100, 0, 0.2) == 10
    assert bs_call(100, 100, 0.25, 0.2) == pytest.approx(3.987, abs=0.01)


def test_vertical_loss_bounded_by_debit_and_gain_by_width():
    sc = vertical_scenarios(100, 100, 105, 30 / 365, 0.2, debit=2.0, lot=50, spread_haircut=0.1,
                            spot_moves=[-0.2, 0.0, 0.2], vol_shifts=[-0.1, 0.1], days=[0, 15, 30])
    assert min(s.pnl for s in sc) >= -2.0 * 50 - 1e-9
    assert max(s.pnl for s in sc) <= (5 - 2.0) * 50 + 1e-9
    assert len(sc) == 18


def test_spread_haircut_lowers_pnl():
    a = vertical_scenarios(100, 100, 105, 0.1, 0.2, 2.0, 50, spread_haircut=0.0,
                           spot_moves=[0.05], vol_shifts=[0.0], days=[5])[0].pnl
    b = vertical_scenarios(100, 100, 105, 0.1, 0.2, 2.0, 50, spread_haircut=0.5,
                           spot_moves=[0.05], vol_shifts=[0.0], days=[5])[0].pnl
    assert b < a


def test_vertical_wrong_strikes_refused():
    with pytest.raises(ValueError):
        vertical_scenarios(100, 105, 100, 0.1, 0.2, 2, 50, spread_haircut=0, spot_moves=[0],
                           vol_shifts=[0], days=[0])


def test_unfilled_leg_contingency():
    assert unfilled_leg_loss(5.0, 4.6, 50) == pytest.approx(-20.0)


def test_route_prefers_best_implementable_net_and_keeps_clock():
    etf = Wrapper("NIFTYBEES", "ETF", 0.10, 0.001, 0.0005, 0.002, 0.0, "exchange fill")
    mf = Wrapper("Index fund direct", "MF", 0.10, 0.0, 0.0, 0.002, 0.01, "NAV allotment T+1")
    best, net = route_exposure([etf, mf])
    assert best.name == "NIFTYBEES" and net == pytest.approx(0.0965)
    assert best.clock != mf.clock
