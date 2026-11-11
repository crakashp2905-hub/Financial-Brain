from __future__ import annotations

import math

import pytest

from financial_brain.kernel.portfolio_risk import (
    group_breaches,
    historical_var_cvar,
    liquidation_days,
    marginal_risk_contributions,
    max_drawdown,
    participation_ok,
    profit_factor,
    time_weighted_return,
)


def test_profit_factor_and_none_without_losses() -> None:
    assert profit_factor([100.0, -50.0, 50.0]) == pytest.approx(3.0)
    assert profit_factor([10.0, 5.0]) is None


def test_max_drawdown_and_time_weighted_return() -> None:
    assert max_drawdown([100.0, 120.0, 90.0, 110.0]) == pytest.approx(0.25)
    assert time_weighted_return([0.10, -0.05]) == pytest.approx(0.045)


def test_marginal_contributions_sum_to_portfolio_volatility() -> None:
    cov = [[0.04, 0.01], [0.01, 0.09]]
    w = [0.6, 0.4]
    contribs = marginal_risk_contributions(cov, w)
    var = sum(w[i] * cov[i][j] * w[j] for i in range(2) for j in range(2))
    assert sum(contribs) == pytest.approx(math.sqrt(var))


def test_group_limits_report_only_breaching_groups() -> None:
    weights = {"A": 0.5, "B": 0.4, "C": 0.1}
    groups = {"A": "IT", "B": "IT", "C": "BANK"}
    assert group_breaches(weights, groups, limit=0.8) == {"IT": pytest.approx(0.9)}
    with pytest.raises(KeyError):
        group_breaches({"Z": 0.1}, groups, 0.5)


def test_liquidation_days_and_participation_limit() -> None:
    assert liquidation_days(10_000, adv=100_000, participation=0.1) == pytest.approx(1.0)
    assert participation_ok(10_000, 100_000, 0.1, max_days=2.0) is True
    assert participation_ok(50_000, 100_000, 0.1, max_days=2.0) is False


def test_historical_var_and_cvar() -> None:
    returns = [-0.10, -0.05, -0.02, 0.01, 0.03, 0.02, 0.00, 0.01, -0.01, 0.04]
    var, cvar = historical_var_cvar(returns, alpha=0.2)
    assert var == pytest.approx(0.05)
    assert cvar == pytest.approx(0.075)
    with pytest.raises(ValueError):
        historical_var_cvar(returns, alpha=0.0)
