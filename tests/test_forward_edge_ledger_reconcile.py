from datetime import date
from decimal import Decimal

import pytest

from financial_brain.forward_edge.ledger_reconcile import (
    PortfolioLedgerMismatch,
    reconcile_portfolio_day,
    require_reconciled,
)
from financial_brain.forward_edge.portfolio_alpha import PortfolioDay
from financial_brain.kernel.ledger import CREDIT, DEBIT, DoubleEntryLedger, Entry, Transaction
from financial_brain.kernel.units import Money


def _ledger(*, fees="10"):
    ledger = DoubleEntryLedger()
    ledger.post(Transaction("pnl", (Entry("realized_pnl", CREDIT, Money.of("100", "INR")),
                                    Entry("cash", DEBIT, Money.of("100", "INR")))))
    ledger.post(Transaction("fee", (Entry("fees", DEBIT, Money.of(fees, "INR")),
                                     Entry("cash", CREDIT, Money.of(fees, "INR")))))
    ledger.post(Transaction("funding", (Entry("financing", DEBIT, Money.of("5", "INR")),
                                         Entry("cash", CREDIT, Money.of("5", "INR")))))
    return ledger


def _day():
    return PortfolioDay(date(2026, 11, 16), 1000, {"ABC": 100}, {"fees": 10}, 5, 0,
                       0.01, 500, 500)


def test_daily_after_cost_portfolio_pnl_reconciles_to_ledger_accounts() -> None:
    result = reconcile_portfolio_day(_day(), DoubleEntryLedger(), _ledger(),
                                     pnl_accounts=("realized_pnl", "fees", "financing"))
    assert result.expected_net_pnl == Decimal("85")
    assert result.ledger_net_pnl == Decimal("85")
    assert result.difference == Decimal("0")
    assert result.reconciled
    require_reconciled(result)


def test_mismatch_is_explicit_and_can_fail_closed() -> None:
    result = reconcile_portfolio_day(_day(), DoubleEntryLedger(), _ledger(fees="20"),
                                     pnl_accounts=("realized_pnl", "fees", "financing"))
    assert not result.reconciled
    with pytest.raises(PortfolioLedgerMismatch, match="does not reconcile"):
        require_reconciled(result)


def test_ledger_reconciliation_requires_explicit_pnl_accounts() -> None:
    with pytest.raises(ValueError, match="account names"):
        reconcile_portfolio_day(_day(), DoubleEntryLedger(), _ledger(), pnl_accounts=())
