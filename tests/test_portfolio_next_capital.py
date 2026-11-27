from __future__ import annotations

import pandas as pd

from financial_brain.portfolio.next_capital import allocate, compare, reason


def test_allocator_keeps_entire_budget_in_cash_when_candidates_fail_cash_hurdle() -> None:
    candidates = pd.DataFrame([{"ticker": "NSE:INFY", "net": 0.01, "cost": 0.002}])
    covariance = pd.DataFrame([[0.04]], index=["NSE:INFY"], columns=["NSE:INFY"])

    allocation = allocate(candidates, covariance, {}, cash_return=0.02,
                          budget=100_000.0, step=10_000.0, horizon=60)
    comparison = compare(candidates, covariance, {}, cash_return=0.02,
                        budget=100_000.0, step=10_000.0, horizon=60)

    assert allocation["allocation"] == {}
    assert allocation["cash"] == 100_000.0
    assert allocation["expected_excess_return_vs_all_cash_inr"] == 0.0
    assert comparison.iloc[0]["clears_cash_hurdle"] == 0
    assert reason(comparison).startswith("keep cash: no candidate clears")
