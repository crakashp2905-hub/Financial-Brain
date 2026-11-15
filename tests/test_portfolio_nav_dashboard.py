from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path

import pytest

from financial_brain.forward_edge import cockpit
from financial_brain.forward_edge.eod_paper_archive import close_and_archive_paper_session
from financial_brain.kernel.oms import State
from financial_brain.kernel.paper_account import PaperAccount


IST = timezone(timedelta(hours=5, minutes=30))


def test_portfolio_nav_matches_every_canonical_session_and_includes_open_positions(tmp_path):
    account = PaperAccount(tmp_path / "paper-account.jsonl", account_id="nav-demo",
                           initial_cash=Decimal("10000"))
    archive_dir = tmp_path / "archives"
    archives = []
    for day in (date(2026, 11, 12), date(2026, 11, 13)):
        marks = {}
        if day.day == 13:
            account.submit_intent(client_id="buy-1", symbol="NSE:INFY", side="BUY", quantity=3,
                                  limit=Decimal("100"), reserve=Decimal("300"))
            account.move("buy-1", State.SUBMITTED)
            account.move("buy-1", State.ACCEPTED)
            account.record_fill("buy-1", fill_id="fill-1", quantity=3, price=Decimal("100"))
            marks = {"NSE:INFY": {"price": "95", "observed_at": datetime(
                2026, 11, 13, 15, 20, tzinfo=IST).isoformat(), "source_id": "fixture-close",
                "source_sha256": "a" * 64}}
        result = close_and_archive_paper_session(
            account=account, session_date=day,
            captured_at=datetime(day.year, day.month, day.day, 15, 25, tzinfo=IST),
            marks=marks, archive_dir=archive_dir)
        archives.append(json.loads(Path(result["archive_path"]).read_text(encoding="utf-8")))

    html = cockpit.portfolio_nav(archives, account.nav_snapshots)
    assert "all 2 sessions reconcile" in html
    assert "Portfolio NAV" in html and "Open positions" in html
    assert "2026-11-12" in html and "2026-11-13" in html
    assert "10000" in html and "285" in html and "9985" in html


def test_portfolio_nav_refuses_missing_canonical_ledger_session(tmp_path):
    account = PaperAccount(tmp_path / "paper-account.jsonl", account_id="cash-only",
                           initial_cash=Decimal("1000"))
    day = date(2026, 11, 12)
    result = close_and_archive_paper_session(
        account=account, session_date=day,
        captured_at=datetime(2026, 11, 12, 15, 25, tzinfo=IST), marks={},
        archive_dir=tmp_path / "archives")
    archive = json.loads(Path(result["archive_path"]).read_text(encoding="utf-8"))
    with pytest.raises(cockpit.CockpitError, match="coverage mismatch"):
        cockpit.portfolio_nav([archive], [])


def test_portfolio_nav_refuses_archive_tampering(tmp_path):
    account = PaperAccount(tmp_path / "paper-account.jsonl", account_id="cash-only",
                           initial_cash=Decimal("1000"))
    day = date(2026, 11, 12)
    result = close_and_archive_paper_session(
        account=account, session_date=day,
        captured_at=datetime(2026, 11, 12, 15, 25, tzinfo=IST), marks={},
        archive_dir=tmp_path / "archives")
    archive = json.loads(Path(result["archive_path"]).read_text(encoding="utf-8"))
    archive["cash"] = "0"
    with pytest.raises(cockpit.CockpitError, match="archive hash"):
        cockpit.portfolio_nav([archive], account.nav_snapshots)
