"""Provenance-bound multi-currency cash conversion and FX translation accounting.

This is a ledger primitive for paper books. It accepts explicit fixings and never
fetches or invents exchange rates. Each conversion balances independently in every
currency through FX clearing accounts; translation P&L is posted in reporting
currency as a separate event.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from financial_brain.kernel.ledger import CREDIT, DEBIT, DoubleEntryLedger, Entry, Transaction
from financial_brain.kernel.units import Money, to_decimal


class MultiCurrencyError(ValueError):
    """Invalid fixing or cross-currency accounting request."""


@dataclass(frozen=True)
class FxFixing:
    currency: str
    rate_to_reporting: Decimal
    effective_at: datetime
    observed_at: datetime
    source_id: str
    source_sha256: str

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Z]{3}", self.currency):
            raise MultiCurrencyError("fixing currency must be a three-letter uppercase code")
        object.__setattr__(self, "rate_to_reporting", to_decimal(self.rate_to_reporting))
        if self.rate_to_reporting <= 0:
            raise MultiCurrencyError("fixing rate must be positive")
        for label, stamp in (("effective_at", self.effective_at), ("observed_at", self.observed_at)):
            if stamp.tzinfo is None or stamp.utcoffset() is None:
                raise MultiCurrencyError(f"{label} must be timezone-aware")
        if not self.source_id or not re.fullmatch(r"[0-9a-f]{64}", self.source_sha256):
            raise MultiCurrencyError("fixing requires a source identifier and lowercase SHA-256")

    def usable_at(self, as_of: datetime) -> None:
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise MultiCurrencyError("as_of must be timezone-aware")
        if self.effective_at > as_of or self.observed_at > as_of:
            raise MultiCurrencyError("fixing was not yet effective and observed at the requested time")


class MultiCurrencyBook:
    """Double-entry ledger helpers for an account with a fixed reporting currency."""

    def __init__(self, *, account_id: str, reporting_currency: str, ledger: DoubleEntryLedger | None = None) -> None:
        if not account_id or not re.fullmatch(r"[A-Z]{3}", reporting_currency):
            raise MultiCurrencyError("account id and three-letter reporting currency are required")
        self.account_id = account_id
        self.reporting_currency = reporting_currency
        self.ledger = ledger or DoubleEntryLedger()

    def convert_cash(self, *, txn_id: str, source: Money, destination: Money,
                     source_fixing: FxFixing, destination_fixing: FxFixing,
                     as_of: datetime, tolerance_reporting: Decimal = Decimal("0.01")) -> bool:
        """Exchange cash using dated fixings; all conversion legs balance per currency.

        ``source`` and ``destination`` are explicit settled amounts. Their fair-value
        difference under supplied fixings must fit within a caller-selected tolerance.
        A live FX spread/fee schedule is intentionally not inferred here.
        """
        source_fixing.usable_at(as_of)
        destination_fixing.usable_at(as_of)
        if source.currency != source_fixing.currency or destination.currency != destination_fixing.currency:
            raise MultiCurrencyError("money currency does not match its fixing")
        if source.amount <= 0 or destination.amount <= 0:
            raise MultiCurrencyError("conversion amounts must be positive")
        tolerance = to_decimal(tolerance_reporting)
        if tolerance < 0:
            raise MultiCurrencyError("conversion tolerance cannot be negative")
        source_value = source.amount * source_fixing.rate_to_reporting
        destination_value = destination.amount * destination_fixing.rate_to_reporting
        if abs(source_value - destination_value) > tolerance:
            raise MultiCurrencyError("conversion amounts do not reconcile to the supplied fixings")
        source_clearing = f"fx_clearing:{self.account_id}:{source.currency}"
        destination_clearing = f"fx_clearing:{self.account_id}:{destination.currency}"
        return self.ledger.post(Transaction(txn_id, (
            Entry(source_clearing, DEBIT, source),
            Entry(f"cash:{self.account_id}", CREDIT, source),
            Entry(f"cash:{self.account_id}", DEBIT, destination),
            Entry(destination_clearing, CREDIT, destination),
        )))

    def revalue_cash(self, *, txn_id: str, currency: str, cash_amount: Decimal,
                     previous: FxFixing, current: FxFixing, as_of: datetime) -> Decimal:
        """Post explicit FX translation P&L for a foreign-currency cash balance."""
        previous.usable_at(as_of)
        current.usable_at(as_of)
        amount = to_decimal(cash_amount)
        if currency == self.reporting_currency:
            raise MultiCurrencyError("reporting-currency cash has no FX translation")
        if previous.currency != currency or current.currency != currency:
            raise MultiCurrencyError("fixing currency does not match cash currency")
        if previous.effective_at >= current.effective_at:
            raise MultiCurrencyError("current fixing must follow the previous effective fixing")
        expected_cash = self.ledger.balance(f"cash:{self.account_id}", currency)
        if amount != expected_cash:
            raise MultiCurrencyError("revaluation amount must equal the ledger cash balance")
        delta = amount * (current.rate_to_reporting - previous.rate_to_reporting)
        if delta == 0:
            return Decimal(0)
        magnitude = Money(abs(delta), self.reporting_currency)
        translation = f"fx_translation:{self.account_id}:{currency}"
        pnl = f"fx_pnl:{self.account_id}"
        entries = ((Entry(translation, DEBIT, magnitude), Entry(pnl, CREDIT, magnitude)) if delta > 0
                   else (Entry(pnl, DEBIT, magnitude), Entry(translation, CREDIT, magnitude)))
        self.ledger.post(Transaction(txn_id, entries))
        return delta

    def fx_pnl(self) -> Money:
        """Cumulative realized/unrealized translation P&L posted in reporting currency."""
        return Money(-self.ledger.balance(f"fx_pnl:{self.account_id}", self.reporting_currency),
                     self.reporting_currency)
