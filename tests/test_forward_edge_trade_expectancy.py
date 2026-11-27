from __future__ import annotations

import pytest

from financial_brain.forward_edge.trade_expectancy import (
    CostedTradeOutcome,
    TradeExpectancyError,
    calculate_trade_expectancy,
)


def _trade(identity: str, gross: float, fees: float = 0.0, **extra):
    values = dict(execution_costs_inr=0.0, fees_inr=fees, taxes_inr=0.0,
                  financing_inr=0.0, other_costs_inr=0.0)
    values.update(extra)
    return CostedTradeOutcome(identity, gross, **values)


def test_expectancy_and_break_even_probability_are_from_net_trade_distribution():
    report = calculate_trade_expectancy(trades=(
        _trade("win", 120, fees=20), _trade("loss", -50, fees=10),
        _trade("flat", 5, fees=5)), unresolved_outcomes=1)
    assert report["positive_net_trades"] == 1
    assert report["negative_net_trades"] == 1
    assert report["flat_net_trades"] == 1
    assert report["observed_net_win_rate"] == pytest.approx(1 / 3)
    assert report["average_net_win_inr"] == 100
    assert report["average_net_loss_inr"] == -60
    assert report["expectancy_net_inr_per_resolved_trade"] == pytest.approx(40 / 3)
    assert report["break_even_win_probability"] == pytest.approx(60 / 160)
    assert report["total_itemized_costs_inr"] == 35
    assert report["estimate_complete"] is False


def test_expectancy_refuses_missing_costs_duplicate_trades_and_invalid_ids():
    with pytest.raises(TradeExpectancyError, match="costs must be complete"):
        _trade("missing", 10, costs_complete=False)
    with pytest.raises(TradeExpectancyError, match="unique"):
        calculate_trade_expectancy(trades=(_trade("same", 1), _trade("same", -1)))
    with pytest.raises(TradeExpectancyError, match="non-negative integer"):
        calculate_trade_expectancy(trades=(_trade("one", 1),), unresolved_outcomes=-1)
