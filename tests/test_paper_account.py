from __future__ import annotations

from decimal import Decimal
from datetime import date, datetime, timezone

import pytest

from financial_brain.kernel.oms import InsufficientCash, OmsError, State
from financial_brain.kernel.paper_account import FeeSchedule, PaperAccount, PaperAccountError


def _buy(book: PaperAccount, client_id: str = "buy-1", quantity: int = 10) -> None:
    book.submit_intent(
        client_id=client_id, symbol="NSE:ABC", side="BUY", quantity=quantity,
        limit=Decimal("100"), reserve=Decimal("1000"),
    )
    book.move(client_id, State.SUBMITTED)
    book.move(client_id, State.ACCEPTED)


def test_buy_sell_fees_and_restart_reconcile(tmp_path) -> None:
    path = tmp_path / "paper.jsonl"
    book = PaperAccount(path, account_id="swing", initial_cash=Decimal("10000"))
    _buy(book)
    book.record_fill("buy-1", fill_id="f1", quantity=10, price=Decimal("90"))
    book.record_fee("fee-1", Decimal("2.50"))
    assert book.cash == Decimal("9097.50")
    assert book.position("NSE:ABC").quantity == 10
    assert book.position("NSE:ABC").average_cost == Decimal("90")

    book.submit_intent(
        client_id="sell-1", symbol="NSE:ABC", side="SELL", quantity=10,
        limit=Decimal("110"), reserve=Decimal("0"),
    )
    book.move("sell-1", State.SUBMITTED)
    book.move("sell-1", State.ACCEPTED)
    book.record_fill("sell-1", fill_id="f2", quantity=10, price=Decimal("110"))
    assert book.position("NSE:ABC").quantity == 0
    assert book.realized_pnl == Decimal("200")

    reopened = PaperAccount(path, account_id="swing", initial_cash=Decimal("10000.00"))
    assert reopened.cash == Decimal("10197.50")
    assert reopened.ledger.balance("cash:swing", "INR") == reopened.cash
    assert reopened.ledger.trial_balance_ok()
    assert reopened.realized_pnl == Decimal("200")
    assert reopened.fees == Decimal("2.50")


def test_replayed_intent_fill_and_fee_have_one_economic_effect(tmp_path) -> None:
    path = tmp_path / "paper.jsonl"
    book = PaperAccount(path, account_id="day", initial_cash=Decimal("5000"))
    _buy(book, "b", 5)
    before = book.journal.seq
    book.submit_intent(
        client_id="b", symbol="NSE:ABC", side="BUY", quantity=5,
        limit=Decimal("100"), reserve=Decimal("1000"),
    )
    assert book.journal.seq == before
    book.record_fill("b", fill_id="same", quantity=5, price=Decimal("100"))
    book.record_fill("b", fill_id="same", quantity=5, price=Decimal("100"))
    book.record_fee("fee", Decimal("1"))
    book.record_fee("fee", Decimal("1"))
    reopened = PaperAccount(path, account_id="day", initial_cash=Decimal("5000"))
    assert reopened.cash == Decimal("4499")
    assert reopened.position("NSE:ABC").quantity == 5
    assert len(reopened.ledger) == 3  # opening, one fill, one fee


def test_sell_reservations_prevent_overselling_and_release_on_cancel(tmp_path) -> None:
    book = PaperAccount(tmp_path / "paper.jsonl", account_id="day", initial_cash=Decimal("5000"))
    _buy(book, "b", 10)
    book.record_fill("b", fill_id="f", quantity=10, price=Decimal("100"))
    book.submit_intent(
        client_id="s1", symbol="NSE:ABC", side="SELL", quantity=7,
        limit=Decimal("105"), reserve=Decimal("0"),
    )
    with pytest.raises(PaperAccountError, match="unreserved"):
        book.submit_intent(
            client_id="s2", symbol="NSE:ABC", side="SELL", quantity=4,
            limit=Decimal("105"), reserve=Decimal("0"),
        )
    book.move("s1", State.SUBMITTED)
    book.move("s1", State.ACCEPTED)
    book.move("s1", State.CANCEL_PENDING)
    book.move("s1", State.CANCELED)
    book.submit_intent(
        client_id="s2", symbol="NSE:ABC", side="SELL", quantity=10,
        limit=Decimal("105"), reserve=Decimal("0"),
    )


def test_failed_fill_or_fee_does_not_change_journal(tmp_path) -> None:
    book = PaperAccount(tmp_path / "paper.jsonl", account_id="day", initial_cash=Decimal("1000"))
    _buy(book, "b", 5)
    seq = book.journal.seq
    with pytest.raises(OmsError):
        book.record_fill("b", fill_id="bad", quantity=6, price=Decimal("100"))
    assert book.journal.seq == seq
    with pytest.raises(InsufficientCash):
        book.record_fee("too-large", Decimal("1000"))
    assert book.journal.seq == seq
    reopened = PaperAccount(book.path, account_id="day", initial_cash=Decimal("1000"))
    assert reopened.cash == Decimal("1000")


def test_conflicting_client_fill_and_fee_ids_are_refused(tmp_path) -> None:
    book = PaperAccount(tmp_path / "paper.jsonl", account_id="day", initial_cash=Decimal("5000"))
    _buy(book, "b", 5)
    book.record_fill("b", fill_id="f", quantity=5, price=Decimal("100"))
    with pytest.raises(PaperAccountError, match="fill id"):
        book.record_fill("b", fill_id="f", quantity=4, price=Decimal("100"))
    book.record_fee("fee", Decimal("1"))
    with pytest.raises(PaperAccountError, match="fee id"):
        book.record_fee("fee", Decimal("2"))


def test_effective_fee_schedule_uses_filled_notional_and_reconciles_nav(tmp_path) -> None:
    path = tmp_path / "fee-nav.jsonl"
    schedule = FeeSchedule(
        "owner-rate-v1", date(2026, 11, 12), Decimal("10"), Decimal("20"),
    )
    book = PaperAccount(path, account_id="nav", initial_cash=Decimal("10000"), fee_schedule=schedule)
    book.submit_intent(
        client_id="buy", symbol="NSE:ABC", side="BUY", quantity=10,
        limit=Decimal("100"), reserve=Decimal("1001"),
    )
    book.move("buy", State.SUBMITTED)
    book.move("buy", State.ACCEPTED)
    before = book.journal.seq
    with pytest.raises(PaperAccountError, match="not yet effective"):
        book.record_fill("buy", fill_id="too-early", quantity=10, price=Decimal("90"),
                         filled_on=date(2026, 11, 11))
    assert book.journal.seq == before

    book.record_fill("buy", fill_id="buy-fill", quantity=10, price=Decimal("90"),
                     filled_on=date(2026, 11, 12))
    assert book.cash == Decimal("9099.10")  # 900 notional + 0.90 fee
    book.record_mark("NSE:ABC", mark_id="mark-new", price=Decimal("100"),
                     observed_at=datetime(2026, 11, 12, 10, tzinfo=timezone.utc))
    book.record_mark("NSE:ABC", mark_id="mark-old", price=Decimal("1"),
                     observed_at=datetime(2026, 11, 12, 9, tzinfo=timezone.utc))
    assert book.equity == Decimal("10099.10")
    assert book.net_pnl == Decimal("99.10")

    book.submit_intent(
        client_id="sell", symbol="NSE:ABC", side="SELL", quantity=10,
        limit=Decimal("110"), reserve=Decimal("0"),
    )
    book.move("sell", State.SUBMITTED)
    book.move("sell", State.ACCEPTED)
    book.record_fill("sell", fill_id="sell-fill", quantity=10, price=Decimal("110"),
                     filled_on=date(2026, 11, 12))
    assert book.cash == Decimal("10196.90")  # 1100 proceeds less 2.20 fee
    assert book.realized_pnl == Decimal("200")
    assert book.fees == Decimal("3.10")
    assert book.net_realized_pnl == Decimal("196.90")
    assert book.equity == Decimal("10196.90")
    assert book.net_pnl == Decimal("196.90")
    assert book.ledger.balance("cash:nav", "INR") == book.cash
    assert book.ledger.trial_balance_ok()

    reopened = PaperAccount(path, account_id="nav", initial_cash=Decimal("10000"))
    assert reopened.fee_schedule == schedule
    assert reopened.cash == book.cash
    assert reopened.fees == book.fees
    assert reopened.equity == book.equity
    with pytest.raises(PaperAccountError, match="fee schedule"):
        PaperAccount(path, account_id="nav", initial_cash=Decimal("10000"),
                     fee_schedule=FeeSchedule("other", date(2026, 11, 12), Decimal("10"), Decimal("20")))

