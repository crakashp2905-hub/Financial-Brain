from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from financial_brain.kernel.ledger import CREDIT, DEBIT, DoubleEntryLedger, Entry, Transaction
from financial_brain.kernel.multi_currency import FxFixing, MultiCurrencyBook, MultiCurrencyError
from financial_brain.kernel.units import Money


NOW = datetime(2026, 11, 12, 10, tzinfo=timezone.utc)


def fixing(currency: str, rate: str, *, age: int = 1) -> FxFixing:
    return FxFixing(currency, Decimal(rate), NOW - timedelta(days=age), NOW - timedelta(hours=age),
                    "OWNER_OR_PROVIDER_FIXTURE", "a" * 64)


def test_cross_currency_conversion_balances_each_currency_and_fx_revaluation_is_explicit():
    ledger = DoubleEntryLedger()
    ledger.post(Transaction("opening-usd", (
        Entry("cash:book", DEBIT, Money.of("100", "USD")),
        Entry("opening_equity:book", CREDIT, Money.of("100", "USD")),
    )))
    book = MultiCurrencyBook(account_id="book", reporting_currency="INR", ledger=ledger)
    prior = fixing("USD", "83", age=2)
    current = fixing("USD", "84", age=1)
    assert book.revalue_cash(txn_id="usd-fx-gain", currency="USD", cash_amount=Decimal("100"),
                             previous=prior, current=current, as_of=NOW) == Decimal("100")
    assert book.fx_pnl() == Money.of("100", "INR")
    assert book.convert_cash(txn_id="convert", source=Money.of("100", "USD"), destination=Money.of("8300", "INR"),
                             source_fixing=fixing("USD", "83"), destination_fixing=fixing("INR", "1"), as_of=NOW)
    assert ledger.balance("cash:book", "USD") == 0
    assert ledger.balance("cash:book", "INR") == 8300
    assert ledger.trial_balance_ok()
    assert book.fx_pnl() == Money.of("100", "INR")
    assert ledger.trial_balance_ok()


def test_fx_fixings_and_conversion_fail_closed_on_bad_or_future_provenance():
    with pytest.raises(MultiCurrencyError, match="aware"):
        FxFixing("USD", Decimal("83"), datetime(2026, 11, 11), NOW, "src", "a" * 64)
    book = MultiCurrencyBook(account_id="book", reporting_currency="INR")
    future = FxFixing("USD", Decimal("83"), NOW + timedelta(minutes=2), NOW + timedelta(minutes=2),
                      "src", "b" * 64)
    with pytest.raises(MultiCurrencyError, match="not yet"):
        book.convert_cash(txn_id="late", source=Money.of("1", "USD"), destination=Money.of("83", "INR"),
                          source_fixing=future, destination_fixing=fixing("INR", "1"), as_of=NOW)
    with pytest.raises(MultiCurrencyError, match="reconcile"):
        book.convert_cash(txn_id="bad-rate", source=Money.of("1", "USD"), destination=Money.of("82", "INR"),
                          source_fixing=fixing("USD", "83"), destination_fixing=fixing("INR", "1"), as_of=NOW)
