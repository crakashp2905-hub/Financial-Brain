from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from financial_brain.portfolio.canonical_ledger import (
    AssetClass,
    CanonicalPortfolioLedger,
    EventKind,
    PortfolioEvent,
)
from financial_brain.portfolio.equity_reconciliation import (
    EquityReconciliationError,
    reconcile_account_equity,
    require_reconciled,
)


ACCOUNT = "paper-main"
IST = timezone(timedelta(hours=5, minutes=30))
START = datetime(2026, 11, 20, 9, 1, tzinfo=IST)
END = datetime(2026, 11, 20, 15, 30, tzinfo=IST)
HASH = hashlib.sha256(b"paper evidence").hexdigest()


def _event(event_id: str, kind: EventKind, at: datetime, **kwargs: object) -> PortfolioEvent:
    return PortfolioEvent(
        account_id=ACCOUNT, sleeve_id="equity", source_system="paper-test",
        source_event_id=event_id, source_sha256=HASH, event_kind=kind,
        occurred_at=at.isoformat(), **kwargs,
    )


def _ledger(path, *, mark_at_open=True):
    ledger = CanonicalPortfolioLedger(path)
    events = [
        _event("opening", EventKind.CASH_FLOW, START - timedelta(minutes=1),
               cash_delta="100000", metadata={"cash_flow_type": "OPENING_BALANCE"}),
        _event("buy", EventKind.FILL, START - timedelta(seconds=30),
               asset_class=AssetClass.EQUITY, instrument_id="NSE:ABC", side="BUY",
               quantity="10", price="100"),
        _event("mark-open", EventKind.MARK, START if mark_at_open else START - timedelta(days=2),
               asset_class=AssetClass.EQUITY, instrument_id="NSE:ABC", price="100"),
        _event("external", EventKind.CASH_FLOW, START + timedelta(hours=1), cash_delta="500",
               metadata={"flow_type": "EXTERNAL", "flow_id": "deposit-1"}),
        _event("dividend", EventKind.CASH_FLOW, START + timedelta(hours=2), cash_delta="10",
               metadata={"flow_type": "DISTRIBUTION", "source": "issuer"}),
        _event("fee", EventKind.FEE, START + timedelta(hours=2), cash_delta="-20"),
        _event("mark-close", EventKind.MARK, END, asset_class=AssetClass.EQUITY,
               instrument_id="NSE:ABC", price="110"),
    ]
    ledger.import_events(events)
    return ledger


def test_equity_change_reconciles_external_flows_trades_marks_distributions_and_fees(tmp_path):
    result = reconcile_account_equity(
        ledger=_ledger(tmp_path / "equity.sqlite"), account_id=ACCOUNT,
        opening_as_of=START, closing_as_of=END,
    )
    assert result.opening_equity_inr == Decimal("100000.00")
    assert result.closing_equity_inr == Decimal("100590.00")
    assert result.external_flows_inr == Decimal("500.00")
    assert result.net_investment_pnl_inr == Decimal("90.00")
    assert result.difference_inr == Decimal("0.00")
    assert result.reconciled
    assert result.event_count == 7
    assert result.event_sha256 and result.opening_snapshot_sha256 and result.closing_snapshot_sha256
    assert len(result.report_sha256) == 64
    require_reconciled(result)


def test_missing_opening_mark_fails_closed(tmp_path):
    result = reconcile_account_equity(
        ledger=_ledger(tmp_path / "equity.sqlite", mark_at_open=False), account_id=ACCOUNT,
        opening_as_of=START, closing_as_of=END,
    )
    assert not result.reconciled
    assert result.difference_inr is None
    assert result.blockers
    with pytest.raises(EquityReconciliationError, match="did not reconcile"):
        require_reconciled(result)


def test_naive_or_non_increasing_interval_is_rejected(tmp_path):
    ledger = _ledger(tmp_path / "equity.sqlite")
    with pytest.raises(EquityReconciliationError, match="timezone-aware"):
        reconcile_account_equity(ledger=ledger, account_id=ACCOUNT,
                                 opening_as_of=START.replace(tzinfo=None), closing_as_of=END)
    with pytest.raises(EquityReconciliationError, match="increasing reconciliation interval"):
        reconcile_account_equity(ledger=ledger, account_id=ACCOUNT,
                                 opening_as_of=END, closing_as_of=START)


def test_mutated_persisted_event_payload_fails_closed(tmp_path):
    path = tmp_path / "equity.sqlite"
    ledger = _ledger(path)
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT event_json FROM portfolio_events WHERE source_event_id='fee'"
        ).fetchone()
        connection.execute(
            "UPDATE portfolio_events SET event_json=? WHERE source_event_id='fee'",
            (row[0].replace('"cash_delta":"-20"', '"cash_delta":"-30"'),),
        )
    result = reconcile_account_equity(ledger=ledger, account_id=ACCOUNT,
                                      opening_as_of=START, closing_as_of=END)
    assert not result.reconciled
    assert result.difference_inr is None
    assert any(blocker.startswith("LEDGER_UNCERTAIN") for blocker in result.blockers)
