from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from financial_brain.kernel.oms import State
from financial_brain.kernel.paper_account import FeeSchedule, PaperAccount
from financial_brain.kernel.paper_portfolio import PaperAccountPortfolio, PaperPortfolioError


SCHEDULE = FeeSchedule("book-fees-v1", date(2026, 1, 1), Decimal("10"), Decimal("10"))


def _account(path, account_id, cash="1000"):
    return PaperAccount(path, account_id=account_id, initial_cash=Decimal(cash),
                        fee_schedule=SCHEDULE)


def test_portfolio_consolidates_isolated_book_ledgers_and_reconciles_marks(tmp_path):
    momentum = _account(tmp_path / "momentum.jsonl", "momentum", "1000")
    control = _account(tmp_path / "control.jsonl", "control", "500")
    momentum.submit_intent(client_id="buy-1", symbol="NSE:INFY", side="BUY", quantity=2,
                           limit=Decimal("100"), reserve=Decimal("205"))
    momentum.move("buy-1", State.SUBMITTED)
    momentum.move("buy-1", State.ACCEPTED)
    momentum.record_fill("buy-1", fill_id="fill-1", quantity=2, price=Decimal("100"),
                         filled_on=date(2026, 11, 12))
    momentum.record_mark("NSE:INFY", mark_id="mark-1", price=Decimal("101"),
                         observed_at=datetime(2026, 11, 12, 10, 0, tzinfo=timezone.utc))
    momentum.submit_intent(client_id="buy-pending", symbol="NSE:TCS", side="BUY", quantity=1,
                           limit=Decimal("100"), reserve=Decimal("105"))

    portfolio = PaperAccountPortfolio({"momentum": momentum, "control": control})
    report = portfolio.report()

    assert report["ledger_trial_balance_ok"] is True
    assert report["cross_book_cash_netting"] is False
    assert report["books"]["momentum"]["fees"] == "0.20"
    assert report["books"]["momentum"]["equity"] == "1001.80"
    assert report["books"]["control"]["cash"] == "500"
    assert report["books"]["control"]["available_cash"] == "500"
    assert report["books"]["momentum"]["reserved"] == "105"
    assert report["books"]["TOTAL"]["initial_cash"] == "1500"
    assert report["books"]["TOTAL"]["equity"] == "1501.80"


def test_portfolio_keeps_total_nav_unavailable_if_any_book_lacks_a_position_mark(tmp_path):
    first = _account(tmp_path / "first.jsonl", "first")
    second = _account(tmp_path / "second.jsonl", "second")
    second.submit_intent(client_id="buy-2", symbol="NSE:TCS", side="BUY", quantity=1,
                         limit=Decimal("100"), reserve=Decimal("102"))
    second.move("buy-2", State.SUBMITTED)
    second.move("buy-2", State.ACCEPTED)
    second.record_fill("buy-2", fill_id="fill-2", quantity=1, price=Decimal("100"),
                       filled_on=date(2026, 11, 12))

    report = PaperAccountPortfolio({"first": first, "second": second}).report()

    assert report["books"]["second"]["equity"] is None
    assert report["books"]["TOTAL"]["equity"] is None
    assert report["books"]["TOTAL"]["equity_status"] == "MISSING_BOOK_MARK"


def test_portfolio_refuses_ambiguous_duplicate_book_authority(tmp_path):
    account = _account(tmp_path / "shared.jsonl", "book-a")

    with pytest.raises(PaperPortfolioError, match="keys must exactly match"):
        PaperAccountPortfolio({"other-name": account})
    with pytest.raises(PaperPortfolioError, match="multiple books"):
        PaperAccountPortfolio({"book-a": account, "book-b": account})
    same_path = _account(tmp_path / "different.jsonl", "book-b")
    same_path.path = account.path
    with pytest.raises(PaperPortfolioError, match="distinct durable journals"):
        PaperAccountPortfolio({"book-a": account, "book-b": same_path})
