"""Read-only reconciliation and NAV aggregation for isolated cash-equity strategy books.

Each PaperAccount remains the authority for its own orders, reserves, ledger and positions. This module derives a
portfolio view from those append-only subledgers; it does not pool buying power or make transfers between books.
"""
from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal

from financial_brain.kernel.ledger import DoubleEntryLedger, Entry, Transaction
from financial_brain.kernel.paper_account import PaperAccount, PaperAccountError


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
        for component, label in (("oms", "order state"), ("ledger", "ledger state"),
                                 ("_positions", "position state")):
            owners = [id(getattr(account, component)) for account in books.values()]
            if len(set(owners)) != len(owners):
                raise PaperPortfolioError(f"each strategy book must own distinct {label}")
        currencies = {account.currency for account in books.values()}
        if len(currencies) != 1:
            raise PaperPortfolioError("portfolio books must use one currency until FX translation is implemented")
        self.books = dict(sorted(books.items()))
        self.currency = next(iter(currencies))

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
            "cash_yield_income", "external_flow_total", "realized_pnl", "net_realized_pnl"
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
                "cash_yield_income": account.cash_yield_income,
                "external_flow_total": account.external_flow_total,
                "realized_pnl": account.realized_pnl,
                "net_realized_pnl": account.net_realized_pnl,
            }
            totals = {key: totals[key] + values[key] for key in totals}
            net_pnl_after_flows = (equity - account.initial_cash - account.external_flow_total
                                   if equity is not None else None)
            rows[book_id] = {**{key: str(value) for key, value in values.items()},
                             "net_pnl_after_flows": (str(net_pnl_after_flows)
                                                     if net_pnl_after_flows is not None else None),
                             "equity": str(equity) if equity is not None else None,
                             "equity_status": "MARKED" if equity is not None else "MISSING_POSITION_MARK",
                             "ledger_transactions": len(account.ledger.transactions())}
        total_net_pnl = (total_equity - totals["initial_cash"] - totals["external_flow_total"]
                         if all_equity_available else None)
        rows["TOTAL"] = {**{key: str(value) for key, value in totals.items()},
                         "net_pnl_after_flows": str(total_net_pnl) if total_net_pnl is not None else None,
                         "equity": str(total_equity) if all_equity_available else None,
                         "equity_status": "MARKED" if all_equity_available else "MISSING_BOOK_MARK",
                         "ledger_transactions": sum(len(a.ledger.transactions()) for a in self.books.values())}
        return {
            "currency": self.currency,
            "books": rows,
            "time_weighted_return": self._time_weighted_return(),
            "ledger_trial_balance_ok": ledger.trial_balance_ok(),
            "cross_book_cash_netting": False,
            "physical_orders": False,
        }

    def _time_weighted_return(self) -> str | None:
        """Link synchronized book NAVs, removing dated external capital flows from each interval."""
        by_book = {book_id: {row["session_date"]: row for row in account.nav_snapshots}
                   for book_id, account in self.books.items()}
        shared_dates = sorted(set.intersection(*(set(rows) for rows in by_book.values())))
        if len(shared_dates) < 2:
            return None
        navs = {day: sum((Decimal(rows[day]["nav"]) for rows in by_book.values()), Decimal(0))
                for day in shared_dates}
        linked = Decimal(1)
        for previous_day, current_day in zip(shared_dates, shared_dates[1:]):
            previous_nav = navs[previous_day]
            flow = sum((Decimal(rows[current_day]["external_flow"]) for rows in by_book.values()), Decimal(0))
            if previous_nav <= 0:
                raise PaperPortfolioError("time-weighted return needs positive opening NAV")
            linked *= (navs[current_day] - flow) / previous_nav
        return str(linked - Decimal(1))
