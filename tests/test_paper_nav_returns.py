from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from financial_brain.kernel.oms import State
from financial_brain.kernel.paper_account import PaperAccount, PaperAccountError

UTC = timezone.utc


def at(day, hour=10, minute=0):
    return datetime.fromisoformat(f"{day}T{hour:02d}:{minute:02d}:00+00:00")


def test_daily_nav_returns_reconcile_cash_days_flows_open_positions_and_late_fills(tmp_path):
    path = tmp_path / "account.jsonl"
    book = PaperAccount(path, account_id="nav-test", initial_cash=Decimal("1000"))

    book.record_nav_snapshot(date(2026, 11, 9), snapshot_id="nav-1", captured_at=at("2026-11-09"))
    book.record_external_cash_flow("deposit-1", Decimal("50"), occurred_at=at("2026-11-10", 9, 30),
                                   reference="paper funding")
    day_two = book.record_nav_snapshot(date(2026, 11, 10), snapshot_id="nav-2", captured_at=at("2026-11-10"))
    assert Decimal(day_two["nav"]) == Decimal("1050")
    assert Decimal(day_two["external_flow"]) == Decimal("50")

    # This fill arrives after yesterday's snapshot and its open position is marked into today's NAV.
    book.submit_intent(client_id="late-buy", symbol="NSE:ABC", side="BUY", quantity=2,
                       limit=Decimal("100"), reserve=Decimal("200"))
    book.move("late-buy", State.SUBMITTED)
    book.move("late-buy", State.ACCEPTED)
    book.record_fill("late-buy", fill_id="late-fill", quantity=2, price=Decimal("100"))
    book.record_mark("NSE:ABC", mark_id="mark-1", price=Decimal("110"), observed_at=at("2026-11-11"))
    day_three = book.record_nav_snapshot(date(2026, 11, 11), snapshot_id="nav-3", captured_at=at("2026-11-11", 10, 1))
    assert Decimal(day_three["nav"]) == Decimal("1070")

    book.record_mark("NSE:ABC", mark_id="mark-2", price=Decimal("105"), observed_at=at("2026-11-12"))
    day_four = book.record_nav_snapshot(date(2026, 11, 12), snapshot_id="nav-4", captured_at=at("2026-11-12", 10, 1))
    assert Decimal(day_four["nav"]) == Decimal("1060")
    assert book.daily_nav_returns == [Decimal(0), Decimal(20) / Decimal(1050), Decimal(-10) / Decimal(1070)]

    recovered = PaperAccount(path, account_id="nav-test", initial_cash=Decimal("1000"))
    assert recovered.nav_snapshots == book.nav_snapshots
    assert recovered.daily_nav_returns == book.daily_nav_returns


def test_daily_nav_snapshot_rejects_future_marks_late_flows_and_rewrites(tmp_path):
    book = PaperAccount(tmp_path / "account.jsonl", account_id="nav-test", initial_cash=Decimal("1000"))
    book.record_nav_snapshot(date(2026, 11, 9), snapshot_id="nav-1", captured_at=at("2026-11-09"))
    with pytest.raises(PaperAccountError, match="late external flow"):
        book.record_external_cash_flow("late-cash", Decimal("20"), occurred_at=at("2026-11-09", 9, 59),
                                       reference="backdated")
    book.submit_intent(client_id="buy", symbol="NSE:ABC", side="BUY", quantity=1,
                       limit=Decimal("100"), reserve=Decimal("100"))
    book.move("buy", State.SUBMITTED)
    book.move("buy", State.ACCEPTED)
    book.record_fill("buy", fill_id="fill", quantity=1, price=Decimal("100"))
    book.record_mark("NSE:ABC", mark_id="future", price=Decimal("101"), observed_at=at("2026-11-10", 10, 2))
    with pytest.raises(PaperAccountError, match="after its capture time"):
        book.record_nav_snapshot(date(2026, 11, 10), snapshot_id="nav-2", captured_at=at("2026-11-10", 10, 1))
    clean = PaperAccount(tmp_path / "clean.jsonl", account_id="clean", initial_cash=Decimal("1000"))
    clean.record_nav_snapshot(date(2026, 11, 9), snapshot_id="nav-1", captured_at=at("2026-11-09"))
    clean.record_nav_snapshot(date(2026, 11, 10), snapshot_id="nav-2", captured_at=at("2026-11-10", 10, 1))
    assert clean.record_nav_snapshot(date(2026, 11, 10), snapshot_id="nav-2", captured_at=at("2026-11-10", 10, 1)) \
        == clean.nav_snapshots[-1]
    with pytest.raises(PaperAccountError, match="immutable"):
        clean.record_nav_snapshot(date(2026, 11, 10), snapshot_id="nav-2b", captured_at=at("2026-11-10", 10, 2))
