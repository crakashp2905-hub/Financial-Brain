"""Frozen-weight account-size economics, with charges shown outside optimization."""
import pytest

from financial_brain.execution.account_size_sweep import AccountSizeSweepError, sweep_account_sizes


def test_sweep_reports_fixed_charges_taxes_deployment_and_both_returns():
    result = sweep_account_sizes(
        book=[{"ticker": "ABC", "weight": 0.5}, {"ticker": "XYZ", "weight": 0.5}],
        prices={"ABC": 100, "XYZ": 2500}, gross_return_by_symbol={"ABC": 0.02, "XYZ": -0.01},
        account_sizes_inr=(50_000, 200_000), include_impact=False)
    small, large = result.points
    assert small.account_size_inr == 50_000
    assert small.shares_by_symbol[0][0] == "ABC"
    assert small.shares_by_symbol[1][1] == 0 or small.shares_by_symbol[1][1] > 0
    assert small.brokerage_inr == 0  # delivery brokerage is flat zero on this schedule
    assert small.statutory_taxes_inr > 0
    assert small.other_charges_inr >= 0
    assert large.deployed_inr >= small.deployed_inr
    assert large.gross_pnl_inr != small.gross_pnl_inr
    assert result.strategy_weights_optimized is False
    assert len(result.book_sha256) == len(result.cost_schedule_sha256) == len(result.report_sha256) == 64


def test_sweep_rejects_missing_gross_return_or_duplicate_sizes():
    with pytest.raises(AccountSizeSweepError, match="gross return"):
        sweep_account_sizes(book=[{"ticker": "ABC", "weight": 1}], prices={"ABC": 100},
                            gross_return_by_symbol={}, account_sizes_inr=(10_000,))
    with pytest.raises(AccountSizeSweepError, match="unique"):
        sweep_account_sizes(book=[{"ticker": "ABC", "weight": 1}], prices={"ABC": 100},
                            gross_return_by_symbol={"ABC": 0.01}, account_sizes_inr=(10_000, 10_000))
