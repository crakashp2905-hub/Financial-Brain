"""Defined-risk spread construction never treats naked options as a strategy."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from financial_brain.evaluation.options_vertical import vertical_candidates


def _rows():
    return pd.DataFrame([
        {"signal_date": "2020-01-01", "option_type": "CE", "spot": 100, "spot_5_ago": 95,
         "strike": 100, "wing_strike": 105, "entry_close": 7, "exit_close": 8,
         "wing_entry_close": 4, "wing_exit_close": 4.5,
         "entry_contracts": 500, "exit_contracts": 500,
         "wing_entry_contracts": 500, "wing_exit_contracts": 500},
        {"signal_date": "2020-01-01", "option_type": "PE", "spot": 100, "spot_5_ago": 105,
         "strike": 100, "wing_strike": 95, "entry_close": 7, "exit_close": 8,
         "wing_entry_close": 4, "wing_exit_close": 4.5,
         "entry_contracts": 500, "exit_contracts": 500,
         "wing_entry_contracts": 500, "wing_exit_contracts": 500},
    ])


def test_four_spreads_have_finite_defined_risk():
    out = vertical_candidates(_rows())
    assert len(out) == 4
    assert set(out["structure"]) == {"debit", "credit"}
    assert set(out["option_type"]) == {"CE", "PE"}
    assert out["executable_proxy"].all()
    assert out["expiry_risk_points_before_exit_costs"].gt(0).all()
    assert np.isfinite(out["net_proxy"]).all()


def test_missing_wing_exit_is_not_a_profitable_credit_spread():
    rows = _rows().iloc[:1].copy()
    rows["wing_exit_close"] = np.nan
    out = vertical_candidates(rows)
    assert not out["executable_proxy"].any()
    assert out["net_proxy"].isna().all()


def test_cost_stress_degrades_debit_and_credit_pnl():
    low = vertical_candidates(_rows(), haircut_per_side=0)
    high = vertical_candidates(_rows(), haircut_per_side=0.03)
    assert (high["net_proxy"].to_numpy() < low["net_proxy"].to_numpy()).all()


def test_invalid_stale_price_bounds_cannot_be_executed():
    rows = _rows().iloc[:1].copy()
    rows["wing_entry_close"] = 20
    out = vertical_candidates(rows)
    assert not out["executable_proxy"].any()
    assert out["net_proxy"].isna().all()


def test_invalid_cost_inputs_fail_closed():
    with pytest.raises(ValueError):
        vertical_candidates(_rows(), haircut_per_side=-0.1)
