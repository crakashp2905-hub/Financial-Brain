"""Idempotent double-entry ledger (FB-0228, FB-0229).

Every transaction must balance per currency. Posting the same transaction id twice is harmless when the content is
identical (it has no second economic effect) and an error when the content differs (a silent overwrite would be a
second truth). Corrections are reversal transactions that cite the original; nothing is edited in place.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal

from financial_brain.kernel.units import Money, UnitError

DEBIT, CREDIT = "D", "C"


class LedgerError(ValueError):
    pass


class ImbalanceError(LedgerError):
    pass


@dataclass(frozen=True)
class Entry:
    account: str
    side: str
    amount: Money

    def __post_init__(self) -> None:
        if self.side not in (DEBIT, CREDIT):
            raise LedgerError("side must be D or C")
        if not self.account or not self.account.strip():
            raise LedgerError("account name required")
        if self.amount.amount <= 0:
            raise LedgerError("entry amounts are positive; the side carries the sign")


@dataclass(frozen=True)
class Transaction:
    txn_id: str
    entries: tuple[Entry, ...]
    reverses: str | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if not self.txn_id:
            raise LedgerError("transaction id required")
        if not self.entries:
            raise LedgerError("a transaction needs at least one entry")
        object.__setattr__(self, "entries", tuple(self.entries))
        balance_by_currency(self.entries)  # raises on imbalance


def balance_by_currency(entries: Iterable[Entry]) -> dict[str, Decimal]:
    """Signed sum per currency: debits minus credits. Zero for every currency means balanced."""
    totals: dict[str, Decimal] = {}
    for e in entries:
        sign = Decimal(1) if e.side == DEBIT else Decimal(-1)
        totals[e.amount.currency] = totals.get(e.amount.currency, Decimal(0)) + sign * e.amount.amount
    for currency, net in totals.items():
        if net != 0:
            raise ImbalanceError(f"unbalanced in {currency}: net {net}")
    return totals


@dataclass
class DoubleEntryLedger:
    _txns: dict[str, Transaction] = field(default_factory=dict)
    _order: list[str] = field(default_factory=list)

    def post(self, txn: Transaction) -> bool:
        """Returns True when newly posted, False for an identical replay. Raises on a conflicting id."""
        existing = self._txns.get(txn.txn_id)
        if existing is not None:
            if existing == txn:
                return False
            raise LedgerError(f"conflicting content for transaction {txn.txn_id}")
        if txn.reverses is not None:
            original = self._txns.get(txn.reverses)
            if original is None:
                raise LedgerError(f"cannot reverse unknown transaction {txn.reverses}")
            if any(t.reverses == txn.reverses for t in self._txns.values()):
                raise LedgerError(f"transaction {txn.reverses} is already reversed")
            if not txn.reason:
                raise LedgerError("a reversal must state its reason")
        self._txns[txn.txn_id] = txn
        self._order.append(txn.txn_id)
        return True

    def reverse(self, original_id: str, *, new_id: str, reason: str) -> bool:
        original = self._txns.get(original_id)
        if original is None:
            raise LedgerError(f"unknown transaction {original_id}")
        flipped = tuple(
            Entry(e.account, CREDIT if e.side == DEBIT else DEBIT, e.amount) for e in original.entries
        )
        return self.post(Transaction(new_id, flipped, reverses=original_id, reason=reason))

    def balance(self, account: str, currency: str) -> Decimal:
        """Net debit balance of an account in a currency (credits make it negative)."""
        total = Decimal(0)
        for txn_id in self._order:
            for e in self._txns[txn_id].entries:
                if e.account == account and e.amount.currency == currency:
                    total += e.amount.amount if e.side == DEBIT else -e.amount.amount
        return total

    def trial_balance_ok(self) -> bool:
        totals: dict[str, Decimal] = {}
        for txn_id in self._order:
            for c, net in balance_by_currency(self._txns[txn_id].entries).items():
                totals[c] = totals.get(c, Decimal(0)) + net
        return all(v == 0 for v in totals.values())

    def transactions(self) -> tuple[Transaction, ...]:
        """Return the immutable posting sequence for audits and consolidated read-only views."""
        return tuple(self._txns[txn_id] for txn_id in self._order)

    def __len__(self) -> int:
        return len(self._order)


__all__ = [
    "CREDIT",
    "DEBIT",
    "DoubleEntryLedger",
    "Entry",
    "ImbalanceError",
    "LedgerError",
    "Transaction",
    "UnitError",
    "balance_by_currency",
]
