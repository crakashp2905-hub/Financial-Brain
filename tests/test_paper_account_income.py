from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from financial_brain.kernel.oms import State
from financial_brain.kernel.paper_account import PaperAccount, PaperAccountError


SOURCE_SHA = "d" * 64


def _buy(book: PaperAccount, *, dated: bool) -> None:
    book.submit_intent(client_id="buy", symbol="NSE:ABC", side="BUY", quantity=10,
                       limit=Decimal("100"), reserve=Decimal("1000"))
    book.move("buy", State.SUBMITTED)
    book.move("buy", State.ACCEPTED)
    book.record_fill("buy", fill_id="buy-fill", quantity=10, price=Decimal("100"),
                     filled_on=date(2026, 11, 10) if dated else None)


def _yield(book: PaperAccount, *, start=date(2026, 11, 10), end=date(2026, 11, 12),
           payment=date(2026, 11, 12), applied=date(2026, 11, 12), action="cash-yield-1") -> None:
    book.apply_cash_yield(action, effective_start=start, effective_end=end,
                          payment_date=payment, applied_on=applied,
                          annual_rate=Decimal("0.0365"), withholding_rate=Decimal("0.10"),
                          day_count=365, source_id="owner-cash-rate-v1", source_sha256=SOURCE_SHA)


def test_cash_yield_uses_dated_daily_cash_and_reconciles_total_return(tmp_path):
    path = tmp_path / "income.jsonl"
    book = PaperAccount(path, account_id="income", initial_cash=Decimal("10000"))
    _buy(book, dated=True)
    _yield(book)
    _yield(book)  # stable action id is idempotent
    assert book.cash == Decimal("9001.62")
    assert book.cash_yield_income == Decimal("1.80")
    assert book.taxes == Decimal("0.18")
    book.record_mark("NSE:ABC", mark_id="mark-1", price=Decimal("100"),
                     observed_at=datetime(2026, 11, 13, 10, tzinfo=timezone.utc))
    book.record_external_cash_flow("deposit-1", Decimal("500"),
                                   occurred_at=datetime(2026, 11, 13, 9, tzinfo=timezone.utc),
                                   reference="capital added after income period")
    report = book.reconcile_total_return()
    assert report["nav_return_inr"] == "1.62"
    assert report["gross_cash_yield_income_inr"] == "1.80"
    assert report["taxes_inr"] == "0.18"
    assert report["reconciled"] is True
    reopened = PaperAccount(path, account_id="income", initial_cash=Decimal("10000"))
    assert reopened.cash_yield_income == Decimal("1.80")
    assert reopened.reconcile_total_return() == report
    assert reopened.ledger.trial_balance_ok()


def test_cash_yield_refuses_undated_cash_changes(tmp_path):
    book = PaperAccount(tmp_path / "undated.jsonl", account_id="undated", initial_cash=Decimal("10000"))
    _buy(book, dated=False)
    before = book.journal.seq
    with pytest.raises(PaperAccountError, match="dated fills"):
        _yield(book)
    assert book.journal.seq == before


def test_cash_yield_requires_actual_payment_date_and_nonoverlapping_periods(tmp_path):
    book = PaperAccount(tmp_path / "periods.jsonl", account_id="periods", initial_cash=Decimal("10000"))
    before = book.journal.seq
    with pytest.raises(PaperAccountError, match="effective period"):
        _yield(book, payment=date(2026, 11, 11), applied=date(2026, 11, 11))
    assert book.journal.seq == before
    _yield(book)
    with pytest.raises(PaperAccountError, match="cannot overlap"):
        _yield(book, start=date(2026, 11, 11), end=date(2026, 11, 13),
               payment=date(2026, 11, 13), applied=date(2026, 11, 13), action="overlap")


def test_late_cash_yield_cannot_rewrite_a_captured_nav_date(tmp_path):
    book = PaperAccount(tmp_path / "late.jsonl", account_id="late", initial_cash=Decimal("10000"))
    book.record_nav_snapshot(date(2026, 11, 12), snapshot_id="nav-1",
                             captured_at=datetime(2026, 11, 12, 16, tzinfo=timezone.utc))
    before = book.journal.seq
    with pytest.raises(PaperAccountError, match="late cash-yield"):
        _yield(book)
    assert book.journal.seq == before


def test_total_return_bridge_fails_if_price_or_income_is_unmarked_or_inconsistent(tmp_path):
    book = PaperAccount(tmp_path / "unmarked.jsonl", account_id="unmarked", initial_cash=Decimal("10000"))
    _buy(book, dated=True)
    with pytest.raises(PaperAccountError, match="current marks"):
        book.reconcile_total_return()
