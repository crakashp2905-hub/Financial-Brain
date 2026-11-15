from datetime import date, datetime, timezone
from decimal import Decimal
import json

import pytest

from financial_brain.forward_edge.eod_paper_archive import (
    EodArchiveError,
    close_and_archive_paper_session,
    verify_prior_paper_open,
)
from financial_brain.kernel.oms import State
from financial_brain.kernel.paper_account import PaperAccount


SESSION = date(2026, 11, 12)
MARK_AT = datetime(2026, 11, 12, 10, 0, tzinfo=timezone.utc)
CAPTURE_AT = datetime(2026, 11, 12, 11, 0, tzinfo=timezone.utc)


def _account(path):
    account = PaperAccount(path, account_id="eod-paper", initial_cash=Decimal("10000"))
    account.submit_intent(client_id="buy-1", symbol="NSE:INFY", side="BUY", quantity=5,
                          limit=Decimal("100"), reserve=Decimal("500"))
    account.move("buy-1", State.SUBMITTED)
    account.move("buy-1", State.ACCEPTED)
    account.record_fill("buy-1", fill_id="fill-1", quantity=3, price=Decimal("100"))
    return account


def _marks():
    return {"NSE:INFY": {"price": "102", "observed_at": MARK_AT.isoformat(),
                         "source_id": "paper-close-quote", "source_sha256": "a" * 64}}


def test_close_cancels_remainder_marks_nav_archives_and_restart_reconciles(tmp_path):
    journal = tmp_path / "account.jsonl"
    account = _account(journal)
    result = close_and_archive_paper_session(
        account=account, session_date=SESSION, captured_at=CAPTURE_AT,
        marks=_marks(), archive_dir=tmp_path / "archive",
    )
    assert result["next_open_reconciliation"]["verified"] is True
    assert result["equity"] == "10006"
    assert result["inventory"] == [{
        "symbol": "NSE:INFY", "quantity": 3, "average_cost": "100",
        "mark_price": "102", "market_value": "306",
        "mark_source_id": "paper-close-quote", "mark_source_sha256": "a" * 64,
    }]
    assert account.orders[0]["state"] == "CANCELED"
    assert (account.orders[0]["filled"], account.orders[0]["remaining"]) == (3, 2)
    reopened = PaperAccount(journal, account_id="eod-paper", initial_cash=Decimal("10000"))
    verified = verify_prior_paper_open(account=reopened, archive_path=result["archive_path"])
    assert verified["verified"] is True
    assert verified["prior_nav"] == "10006"
    replay = close_and_archive_paper_session(
        account=reopened, session_date=SESSION, captured_at=CAPTURE_AT,
        marks=_marks(), archive_dir=tmp_path / "archive",
    )
    assert replay["archive_sha256"] == result["archive_sha256"]


def test_missing_marks_refuse_close_without_cancelling_order(tmp_path):
    account = _account(tmp_path / "missing-mark.jsonl")
    with pytest.raises(EodArchiveError, match="mark coverage mismatch"):
        close_and_archive_paper_session(
            account=account, session_date=SESSION, captured_at=CAPTURE_AT,
            marks={}, archive_dir=tmp_path / "archive",
        )
    assert account.orders[0]["state"] == "PARTIAL"
    assert account.nav_snapshots == []


def test_unresolved_order_and_bad_mark_clock_fail_closed(tmp_path):
    account = PaperAccount(tmp_path / "unknown.jsonl", account_id="unknown", initial_cash=Decimal("10000"))
    account.submit_intent(client_id="unknown-order", symbol="NSE:INFY", side="BUY", quantity=1,
                          limit=Decimal("100"), reserve=Decimal("100"))
    account.move("unknown-order", State.SUBMITTED)
    account.move("unknown-order", State.ACCEPTED)
    account.move("unknown-order", State.UNKNOWN)
    with pytest.raises(EodArchiveError, match="unresolved"):
        close_and_archive_paper_session(
            account=account, session_date=SESSION, captured_at=CAPTURE_AT,
            marks={}, archive_dir=tmp_path / "archive",
        )
    held = _account(tmp_path / "bad-mark.jsonl")
    wrong_day = {"NSE:INFY": {**_marks()["NSE:INFY"],
                               "observed_at": datetime(2026, 11, 11, 10, 0, tzinfo=timezone.utc).isoformat()}}
    with pytest.raises(EodArchiveError, match="requested exchange session"):
        close_and_archive_paper_session(
            account=held, session_date=SESSION, captured_at=CAPTURE_AT,
            marks=wrong_day, archive_dir=tmp_path / "archive",
        )


def test_archive_content_hash_detects_tampering(tmp_path):
    account = _account(tmp_path / "tamper.jsonl")
    close_and_archive_paper_session(
        account=account, session_date=SESSION, captured_at=CAPTURE_AT,
        marks=_marks(), archive_dir=tmp_path / "archive",
    )
    path = tmp_path / "archive" / SESSION.isoformat() / "eod-paper.json"
    body = json.loads(path.read_text())
    body["equity"] = "1"
    path.write_text(json.dumps(body))
    with pytest.raises(EodArchiveError, match="hash"):
        verify_prior_paper_open(account=account, archive_path=path)
