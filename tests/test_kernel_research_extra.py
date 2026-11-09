from __future__ import annotations

from decimal import Decimal

import pytest

from financial_brain.kernel.research_extra import (
    CostComponents,
    attribute_costs,
    bootstrap_ci,
    breakeven_cost,
    cost_sweep,
    deflated_sharpe,
    variation_settlement,
)


def test_more_trials_lower_the_deflated_sharpe() -> None:
    one = deflated_sharpe(0.2, n_obs=252, n_trials=1)
    many = deflated_sharpe(0.2, n_obs=252, n_trials=100)
    assert 0.5 < one < 1.0
    assert many < one


def test_deflated_sharpe_validates_inputs() -> None:
    with pytest.raises(ValueError):
        deflated_sharpe(0.1, n_obs=1, n_trials=1)
    with pytest.raises(ValueError):
        deflated_sharpe(0.1, n_obs=10, n_trials=0)


def test_bootstrap_is_seeded_and_brackets_the_mean() -> None:
    values = [0.01, -0.02, 0.03, 0.00, 0.02, -0.01, 0.015, 0.005]
    a = bootstrap_ci(values, seed=7)
    b = bootstrap_ci(values, seed=7)
    assert a == b
    lo, hi = a
    assert lo <= sum(values) / len(values) <= hi


def test_cost_sweep_and_breakeven() -> None:
    gross = [0.02, 0.01, -0.01, 0.03]
    assert breakeven_cost(gross) == pytest.approx(0.0125)
    sweep = dict(cost_sweep(gross, [0.0, 0.0125, 0.02]))
    assert sweep[0.0] == pytest.approx(0.05)
    assert sweep[0.0125] == pytest.approx(0.0)
    assert sweep[0.02] < 0
    assert breakeven_cost([]) is None


def test_cost_attribution_must_sum_to_reported_total() -> None:
    comp = CostComponents(Decimal("1"), Decimal("2"), Decimal("0.5"), Decimal("0.25"), Decimal("0.25"))
    parts = attribute_costs(comp, Decimal("4"))
    assert parts["total"] == Decimal("4")
    with pytest.raises(ValueError, match="sum"):
        attribute_costs(comp, Decimal("5"))
    with pytest.raises(ValueError, match="non-negative"):
        CostComponents(Decimal("-1"), Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"))


def test_variation_uses_prior_day_position() -> None:
    prices = [Decimal("100"), Decimal("102"), Decimal("99")]
    positions = [2, -1, 0]
    var = variation_settlement(prices, positions, Decimal("50"))
    assert var == [Decimal("200"), Decimal("150")]  # (102-100)*2*50, (99-102)*(-1)*50
    with pytest.raises(ValueError):
        variation_settlement(prices, [1], Decimal("1"))
