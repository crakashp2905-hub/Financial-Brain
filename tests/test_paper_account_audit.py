from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from financial_brain.forward_edge.durable import DurableJournal
from financial_brain.kernel.oms import State
from financial_brain.kernel.paper_account import FeeSchedule, PaperAccount
from financial_brain.kernel.paper_account_audit import PaperAuditError, audit_paper_account_journal


def _submit(account, client_id, side, quantity, limit, reserve):
    account.submit_intent(client_id=client_id, symbol="NSE:INFY", side=side,
                          quantity=quantity, limit=Decimal(limit), reserve=Decimal(reserve))
    account.move(client_id, State.SUBMITTED)
    account.move(client_id, State.ACCEPTED)


def test_independent_journal_audit_recomputes_balances_and_daily_nav(tmp_path):
    path = tmp_path / "audit.jsonl"
    schedule = FeeSchedule("owner-cost-v1", date(2026, 10, 5), Decimal("10"), Decimal("10"))
    account = PaperAccount(path, account_id="audit", initial_cash=Decimal("10000"), fee_schedule=schedule)
    _submit(account, "buy", "BUY", 2, "100", "201")
    account.record_fill("buy", fill_id="buy-fill", quantity=2, price=Decimal("100"),
                        filled_on=date(2026, 10, 5))
    account.record_mark("NSE:INFY", mark_id="mark-1", price=Decimal("110"),
                        observed_at=datetime(2026, 10, 5, 10, tzinfo=timezone.utc))
    account.record_nav_snapshot(date(2026, 10, 5), snapshot_id="nav-1",
                                captured_at=datetime(2026, 10, 5, 10, 1, tzinfo=timezone.utc))

    _submit(account, "sell", "SELL", 1, "120", "0")
    account.record_fill("sell", fill_id="sell-fill", quantity=1, price=Decimal("120"),
                        filled_on=date(2026, 10, 6))
    account.record_mark("NSE:INFY", mark_id="mark-2", price=Decimal("130"),
                        observed_at=datetime(2026, 10, 6, 10, tzinfo=timezone.utc))
    account.record_nav_snapshot(date(2026, 10, 6), snapshot_id="nav-2",
                                captured_at=datetime(2026, 10, 6, 10, 1, tzinfo=timezone.utc))

    audit = audit_paper_account_journal(path)
    assert audit["status"] == "RECONCILED"
    assert audit["source"] == "DURABLE_JOURNAL_ONLY"
    assert audit["cash"] == str(account.cash) == "9919.68"
    assert audit["positions"]["NSE:INFY"] == {"quantity": 1, "cost_basis": "100"}
    assert audit["realized_pnl_gross"] == "20"
    assert audit["fees"] == "0.32"
    assert audit["realized_pnl_net"] == "19.68"
    assert audit["current_nav"] == "10049.68"
    assert all(row["reconciled"] for row in audit["nav_snapshots"])


def test_independent_audit_rejects_nav_mismatch_in_a_valid_hash_chain(tmp_path):
    journal = DurableJournal(tmp_path / "bad-nav.jsonl")
    journal.append({"t": "ACCOUNT_OPEN", "account": "audit", "currency": "INR", "cash": "1000"})
    journal.append({"t": "NAV_SNAPSHOT", "snapshot_id": "n1", "session_date": "2026-10-05",
                    "captured_at": "2026-10-05T10:00:00+00:00", "nav": "999", "external_flow": "0"})
    with pytest.raises(PaperAuditError, match="stored NAV does not reconcile"):
        audit_paper_account_journal(journal.path)


def test_independent_audit_rejects_future_cash_flow_in_nav_snapshot(tmp_path):
    journal = DurableJournal(tmp_path / "future-flow.jsonl")
    journal.append({"t": "ACCOUNT_OPEN", "account": "audit", "currency": "INR", "cash": "1000"})
    journal.append({"t": "EXTERNAL_FLOW", "flow_id": "flow-1", "amount": "100",
                    "occurred_at": "2026-10-05T11:00:00+00:00"})
    journal.append({"t": "NAV_SNAPSHOT", "snapshot_id": "n1", "session_date": "2026-10-05",
                    "captured_at": "2026-10-05T10:00:00+00:00", "nav": "1100", "external_flow": "100"})
    with pytest.raises(PaperAuditError, match="future-dated external cash flow"):
        audit_paper_account_journal(journal.path)


def test_independent_audit_rejects_pre_fill_mark(tmp_path):
    journal = DurableJournal(tmp_path / "stale-mark.jsonl")
    journal.append({"t": "ACCOUNT_OPEN", "account": "audit", "currency": "INR", "cash": "1000"})
    journal.append({"t": "MARK", "mark_id": "m1", "symbol": "NSE:INFY", "price": "100",
                    "observed_at": "2026-10-05T10:00:00+00:00"})
    journal.append({"t": "FILL", "client_id": "buy-1", "fill_id": "f1", "symbol": "NSE:INFY",
                    "side": "BUY", "quantity": 1, "price": "100", "fee_amount": "0",
                    "filled_on": "2026-10-06"})
    journal.append({"t": "NAV_SNAPSHOT", "snapshot_id": "n1", "session_date": "2026-10-06",
                    "captured_at": "2026-10-06T10:00:00+00:00", "nav": "1000", "external_flow": "0"})
    with pytest.raises(PaperAuditError, match="pre-fill mark"):
        audit_paper_account_journal(journal.path)
