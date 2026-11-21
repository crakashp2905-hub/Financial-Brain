"""Reconcile daily portfolio P&L to double-entry ledger account movements (FE-0091)."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

from financial_brain.kernel.ledger import DoubleEntryLedger

from .portfolio_alpha import PortfolioDay


class PortfolioLedgerMismatch(ArithmeticError):
    """Daily reported portfolio P&L differs from the accounting ledger."""


@dataclass(frozen=True)
class LedgerPnlReconciliation:
    expected_net_pnl: Decimal
    ledger_net_pnl: Decimal
    difference: Decimal
    reconciled: bool
    pnl_accounts: tuple[str, ...]


def reconcile_portfolio_day(day: PortfolioDay, opening: DoubleEntryLedger, closing: DoubleEntryLedger,
                            *, pnl_accounts: Iterable[str], currency: str = "INR",
                            tolerance: Decimal = Decimal("0.01")) -> LedgerPnlReconciliation:
    """Compare day net P&L to changes in explicit P&L accounts from two ledger snapshots.

    Ledger balances use debit-positive convention, so P&L account net debits are negated.
    Callers must list all realized/unrealized P&L, fee, tax, financing and cash-interest
    accounts used by their accounting policy; this function refuses an empty account set.
    """
    accounts = tuple(sorted(set(pnl_accounts)))
    if not accounts or any(not account.strip() for account in accounts):
        raise ValueError("explicit non-empty P&L account names are required")
    if not currency.strip() or tolerance < 0:
        raise ValueError("currency is required and tolerance cannot be negative")
    if not opening.trial_balance_ok() or not closing.trial_balance_ok():
        raise PortfolioLedgerMismatch("opening or closing double-entry ledger is unbalanced")
    opening_balance = sum((opening.balance(account, currency) for account in accounts), Decimal(0))
    closing_balance = sum((closing.balance(account, currency) for account in accounts), Decimal(0))
    ledger_pnl = -(closing_balance - opening_balance)
    expected = sum((Decimal(str(value)) for value in day.instrument_gross_pnl.values()), Decimal(0))
    expected += Decimal(str(day.cash_interest_pnl))
    expected -= sum((Decimal(str(value)) for value in day.charges.values()), Decimal(0))
    expected -= Decimal(str(day.financing_cost))
    difference = ledger_pnl - expected
    return LedgerPnlReconciliation(expected, ledger_pnl, difference, abs(difference) <= tolerance, accounts)


def require_reconciled(result: LedgerPnlReconciliation) -> None:
    if not result.reconciled:
        raise PortfolioLedgerMismatch(
            f"daily P&L does not reconcile: expected={result.expected_net_pnl}, "
            f"ledger={result.ledger_net_pnl}, difference={result.difference}")
