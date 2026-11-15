from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from financial_brain.forward_edge.durable import DurableJournal
from financial_brain.kernel.oms import State
from financial_brain.kernel.paper_account import FeeSchedule, PaperAccount
from financial_brain.kernel.paper_account_audit import (
    PaperAuditError,
    audit_paper_account_journal,
    reconcile_eod_snapshot,
)


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
    assert audit["fill_ids"] == ["buy-fill", "sell-fill"]
    assert audit["marks"]["NSE:INFY"]["price"] == "130"
    assert all(row["reconciled"] for row in audit["nav_snapshots"])


def test_eod_reconciliation_halts_on_mismatch_and_requires_human_repair_signoff():
    as_of = datetime(2026, 10, 6, 11, tzinfo=timezone.utc)
    journal_state = {
        "positions": {"NSE:INFY": {"quantity": 2, "cost_basis": "200"}},
        "fill_ids": ["fill-1", "fill-2"],
        "marks": {"NSE:INFY": {"price": "110", "observed_at": "2026-10-06T10:00:00+00:00"}},
    }
    clean = {
        "reference_positions": {"NSE:INFY": 2},
        "reference_fill_ids": ["fill-2", "fill-1"],
        "reference_marks": {"NSE:INFY": {"price": "110.01", "observed_at": "2026-10-06T10:05:00+00:00",
                                                  "source_sha256": "a" * 64}},
    }
    mismatch = reconcile_eod_snapshot(journal_state=journal_state, as_of=as_of,
                                      **{**clean, "reference_positions": {"NSE:INFY": 1}})
    assert mismatch["status"] == "HALT_MISMATCH"
    assert mismatch["entries_allowed"] is False

    repaired = reconcile_eod_snapshot(journal_state=journal_state, as_of=as_of, prior_incident_open=True, **clean)
    assert repaired["status"] == "AWAITING_REPAIR_SIGNOFF"
    assert repaired["entries_allowed"] is False
    signed = reconcile_eod_snapshot(
        journal_state=journal_state, as_of=as_of, prior_incident_open=True, **clean,
        repair_signoff={"incident_id": "inc-1", "repair_id": "repair-1", "actor": "owner",
                        "signed_at": "2026-10-06T10:59:00+00:00", "source_sha256": "b" * 64},
    )
    assert signed["status"] == "RECONCILED_SIGNED_OFF"
    assert signed["entries_allowed"] is True

    nonhuman = reconcile_eod_snapshot(
        journal_state=journal_state, as_of=as_of, prior_incident_open=True, **clean,
        repair_signoff={"incident_id": "inc-1", "repair_id": "repair-2", "actor": "automation:reconcile",
                        "signed_at": "2026-10-06T10:59:00+00:00", "source_sha256": "c" * 64},
    )
    assert nonhuman["status"] == "AWAITING_REPAIR_SIGNOFF"
    assert nonhuman["entries_allowed"] is False


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
