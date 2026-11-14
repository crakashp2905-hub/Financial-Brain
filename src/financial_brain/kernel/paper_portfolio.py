"""Read-only reconciliation and NAV aggregation for isolated cash-equity strategy books.

Each PaperAccount remains the authority for its own orders, reserves, ledger and positions. This module derives a
portfolio view from those append-only subledgers; it does not pool buying power or make transfers between books.
"""
from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from pathlib import Path

from financial_brain.forward_edge.durable import DurableJournal
from financial_brain.kernel.ledger import DoubleEntryLedger, Entry, Transaction
from financial_brain.kernel.paper_account import FeeSchedule, PaperAccount, PaperAccountError
from financial_brain.kernel.registered_paper_books import RegisteredPaperBooks


class PaperPortfolioError(ValueError):
    """The isolated paper books cannot be consolidated without ambiguity."""


class PaperAccountPortfolio:
    """A read-only multi-book reconciliation view with no cross-book cash netting."""

    def __init__(self, books: Mapping[str, PaperAccount]) -> None:
        if not books:
            raise PaperPortfolioError("at least one strategy book is required")
        if len({id(account) for account in books.values()}) != len(books):
            raise PaperPortfolioError("one PaperAccount cannot be registered as multiple books")
        if any(not book_id.strip() or account.account_id != book_id for book_id, account in books.items()):
            raise PaperPortfolioError("book keys must exactly match their PaperAccount IDs")
        paths = [account.path.resolve() for account in books.values()]
        if len(set(paths)) != len(paths):
            raise PaperPortfolioError("strategy books must use distinct durable journals")
        currencies = {account.currency for account in books.values()}
        if len(currencies) != 1:
            raise PaperPortfolioError("portfolio books must use one currency until FX translation is implemented")
        self.books = dict(sorted(books.items()))
        self.currency = next(iter(currencies))

    @classmethod
    def from_registered_plan(cls, registry: RegisteredPaperBooks, *, plan_id: str,
                             account_root: Path) -> PaperAccountPortfolio:
        """Discover every account in a frozen plan from its existing durable journal.

        This is intentionally read-only: an absent account, malformed opening record, or
        mismatch against frozen capital is an error; reporting never creates a new book.
        """
        plan = next((item for item in registry.plans if item.plan_id == plan_id), None)
        if plan is None:
            raise PaperPortfolioError("portfolio plan is not present in the verified registry")
        accounts: dict[str, PaperAccount] = {}
        try:
            for allocation in plan.books:
                path = registry.account_path(
                    plan_id=plan_id, account_id=allocation.account_id, root=Path(account_root))
                if not path.is_file():
                    raise PaperPortfolioError(
                        f"registered paper account journal is missing: {allocation.account_id}")
                rows = DurableJournal(path).records()
                if not rows or rows[0].get("t") != "ACCOUNT_OPEN":
                    raise PaperPortfolioError(
                        f"registered paper account has no valid opening record: {allocation.account_id}")
                opening = rows[0]
                if opening.get("account") != allocation.account_id:
                    raise PaperPortfolioError("account journal identity differs from frozen allocation")
                try:
                    opening_cash = Decimal(str(opening["cash"]))
                    allocated_cash = Decimal(allocation.capital_inr)
                except (KeyError, InvalidOperation, ValueError) as exc:
                    raise PaperPortfolioError("account opening capital is invalid") from exc
                if not opening_cash.is_finite() or opening_cash != allocated_cash:
                    raise PaperPortfolioError("account opening capital differs from frozen allocation")
                schedule = FeeSchedule.from_dict(opening.get("fee_schedule"))
                account = PaperAccount(
                    path, account_id=allocation.account_id, initial_cash=opening_cash,
                    currency=str(opening.get("currency", "")), fee_schedule=schedule)
                accounts[account.account_id] = account
        except PaperPortfolioError:
            raise
        except (PaperAccountError, OSError, ValueError, KeyError, TypeError) as exc:
            raise PaperPortfolioError(f"registered account journal failed validation: {exc}") from exc
        return cls(accounts)

    def consolidated_ledger(self) -> DoubleEntryLedger:
        """Rebuild a derived trial-balance view with book-namespaced accounts and transaction IDs."""
        consolidated = DoubleEntryLedger()
        for book_id, account in self.books.items():
            prefix = f"book:{book_id}"
            for transaction in account.ledger.transactions():
                entries = tuple(Entry(f"{prefix}:{entry.account}", entry.side, entry.amount)
                                for entry in transaction.entries)
                txn_id = f"{prefix}:txn:{transaction.txn_id}"
                reverses = f"{prefix}:txn:{transaction.reverses}" if transaction.reverses else None
                consolidated.post(Transaction(txn_id, entries, reverses, transaction.reason))
        return consolidated

    def report(self) -> dict:
        """Return per-book and aggregate cash, P&L, NAV availability and ledger reconciliation facts."""
        ledger = self.consolidated_ledger()
        if not ledger.trial_balance_ok():
            raise PaperPortfolioError("consolidated double-entry ledger does not balance")
        rows: dict[str, dict] = {}
        totals = {key: Decimal(0) for key in (
            "initial_cash", "cash", "reserved", "available_cash", "fees", "taxes", "dividend_income",
            "realized_pnl", "net_realized_pnl"
        )}
        total_equity = Decimal(0)
        all_equity_available = True
        for book_id, account in self.books.items():
            cash_account = f"book:{book_id}:cash:{account.account_id}"
            if ledger.balance(cash_account, account.currency) != account.cash:
                raise PaperPortfolioError(f"consolidated cash balance differs for book {book_id}")
            try:
                equity: Decimal | None = account.equity
            except PaperAccountError:
                equity = None
                all_equity_available = False
            if equity is not None:
                total_equity += equity
            values = {
                "initial_cash": account.initial_cash,
                "cash": account.cash,
                "reserved": account.oms.reserved(book_id),
                "available_cash": account.available_cash,
                "fees": account.fees,
                "taxes": account.taxes,
                "dividend_income": account.dividend_income,
                "realized_pnl": account.realized_pnl,
                "net_realized_pnl": account.net_realized_pnl,
            }
            totals = {key: totals[key] + amount for key, amount in values.items()}
            rows[book_id] = {**{key: str(value) for key, value in values.items()},
                             "equity": str(equity) if equity is not None else None,
                             "equity_status": "MARKED" if equity is not None else "MISSING_POSITION_MARK",
                             "ledger_transactions": len(account.ledger.transactions())}
        rows["TOTAL"] = {**{key: str(value) for key, value in totals.items()},
                         "equity": str(total_equity) if all_equity_available else None,
                         "equity_status": "MARKED" if all_equity_available else "MISSING_BOOK_MARK",
                         "ledger_transactions": sum(len(a.ledger.transactions()) for a in self.books.values())}
        return {
            "currency": self.currency,
            "books": rows,
            "ledger_trial_balance_ok": ledger.trial_balance_ok(),
            "cross_book_cash_netting": False,
            "physical_orders": False,
        }
