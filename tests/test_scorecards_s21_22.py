from __future__ import annotations

from datetime import date
from decimal import Decimal as D

import pytest

from financial_brain.evaluation.scorecards import (
    FrozenRule,
    ScoreError,
    Status,
    reconcile,
    strategy_status,
    time_weighted_return,
    trade_stats,
    xirr,
)


def test_trade_stats():
    s = trade_stats([10, -5, 20, -5])
    assert s.win_rate == 0.5 and s.expectancy == 5 and s.profit_factor == 3


def test_no_trades_raises():
    with pytest.raises(ScoreError):
        trade_stats([])


def test_contribution_is_not_return():
    # 1000 -> 1000 (flat), then +1000 contribution, then 2000 -> 2000
    v = [(date(2026, 1, 1), 1000.0, 0.0), (date(2026, 2, 1), 1000.0, 1000.0),
         (date(2026, 3, 1), 2000.0, 0.0)]
    assert time_weighted_return(v) == pytest.approx(0.0)


def test_twr_chains_subperiods():
    v = [(date(2026, 1, 1), 100.0, 0.0), (date(2026, 2, 1), 110.0, 0.0),
         (date(2026, 3, 1), 121.0, 0.0)]
    assert time_weighted_return(v) == pytest.approx(0.21)


def test_xirr_one_year_ten_percent():
    r = xirr([(date(2025, 1, 1), -100.0), (date(2026, 1, 1), 110.0)])
    assert r == pytest.approx(0.10, abs=1e-4)


def test_desk_pnl_must_reconcile():
    reconcile({"SWING": D(10), "ETF": D(-3)}, D(7))
    with pytest.raises(ScoreError):
        reconcile({"SWING": D(10)}, D(9))


RULE = FrozenRule("v1", min_sessions=20, max_drawdown=0.1, registered=True)


def test_unregistered_is_exploratory():
    r = FrozenRule("v1", 20, 0.1, registered=False)
    assert strategy_status(r, [[0.01]] * 50, 0.0, 0.0) is Status.EXPLORATORY


def test_too_few_sessions_is_collecting():
    assert strategy_status(RULE, [[0.01]] * 5, 0.0, 0.0) is Status.COLLECTING


def test_drawdown_breach_is_failing():
    assert strategy_status(RULE, [[0.01]] * 50, 0.2, 0.0) is Status.FAILING


def test_consistent_excess_is_verified_noise_is_failing():
    good = [[0.01, 0.012]] * 30
    noise = [[0.02, -0.02], [-0.03, 0.01], [0.01, -0.01]] * 10
    assert strategy_status(RULE, good, 0.0, 0.0) is Status.FORWARD_VERIFIED
    assert strategy_status(RULE, noise, 0.0, 0.0) is Status.FAILING
