from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from financial_brain.kernel.portfolio import Book, FillRow, PortfolioError, futures_variation, reconcile

D = date(2026, 11, 9)
S = date(2026, 11, 10)


def _f(side: str, qty: int, px: str, fees: str = "0", settle: date = S) -> FillRow:
    return FillRow("NSE:ABC", side, qty, Decimal(px), Decimal(fees), D, settle)


def test_fifo_realised_pnl_matches_hand_calculation() -> None:
    book = Book("FIFO")
    book.apply(_f("BUY", 10, "100"))
    book.apply(_f("BUY", 10, "110"))
    book.apply(_f("SELL", 15, "120"))
    # 10 @ (120-100)=200, 5 @ (120-110)=50
    assert book.realized_gross == Decimal("250")
    assert book.position("NSE:ABC") == 5


def test_average_cost_differs_from_fifo_on_the_same_fills() -> None:
    fills = [_f("BUY", 10, "100"), _f("BUY", 10, "110"), _f("SELL", 15, "120")]
    fifo, avg = Book("FIFO"), Book("AVERAGE")
    for f in fills:
        fifo.apply(f)
        avg.apply(f)
    assert fifo.realized_gross == Decimal("250")  # 10@20 + 5@10
    assert avg.realized_gross == Decimal("225")  # avg cost 105: 15 x (120-105)
    assert avg.position("NSE:ABC") == 5


def test_average_method_name_accepted_and_fees_reduce_net() -> None:
    book = Book("AVERAGE")
    book.apply(_f("BUY", 10, "100", fees="5"))
    book.apply(_f("SELL", 10, "101", fees="5"))
    assert book.realized_gross == Decimal("10")
    assert book.realized_net() == Decimal("0")


def test_short_then_cover_realises_correct_sign() -> None:
    book = Book()
    book.apply(_f("SELL", 10, "100"))
    book.apply(_f("BUY", 10, "90"))
    assert book.realized_gross == Decimal("100")
    assert book.position("NSE:ABC") == 0


def test_unrealized_requires_a_mark_and_uses_multiplier() -> None:
    book = Book()
    book.apply(FillRow("NSE:FUT", "BUY", 2, Decimal("100"), Decimal("0"), D, S))
    with pytest.raises(PortfolioError, match="refusing to value at zero"):
        book.unrealized({})
    assert book.unrealized({"NSE:FUT": Decimal("103")}, {"NSE:FUT": Decimal("50")}) == Decimal("300")


def test_unsettled_cash_is_not_spendable_before_settlement() -> None:
    book = Book()
    book.apply(_f("BUY", 10, "100"))  # cash out, settles S
    book.apply(_f("SELL", 10, "110", settle=date(2026, 11, 12)))  # proceeds settle later
    # starting cash is zero: on trade date nothing has settled; on the buy's settle day only the outflow has
    assert book.settled_cash(D) == Decimal("0")
    assert book.settled_cash(S) == Decimal("-1000")
    assert book.settled_cash(date(2026, 11, 12)) == Decimal("100")


def test_futures_variation_settles_daily() -> None:
    assert futures_variation(Decimal("100"), Decimal("102"), 3, Decimal("50")) == Decimal("300")
    assert futures_variation(Decimal("100"), Decimal("98"), -2, Decimal("50")) == Decimal("200")


def test_reconciliation_reports_exact_exceptions() -> None:
    assert reconcile({"A": 5}, {"A": 5}) == []
    assert reconcile({"A": 5, "B": 0}, {"A": 4}) == ["A: book 5 vs expected 4"]


def test_bad_method_and_bad_fill_rejected() -> None:
    with pytest.raises(PortfolioError):
        Book("LIFOISH")
    with pytest.raises(PortfolioError):
        Book().apply(_f("BUY", 0, "1"))
