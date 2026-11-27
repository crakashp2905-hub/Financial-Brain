from __future__ import annotations

import hashlib
import sqlite3
from threading import Event, Thread
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
    EquityReconciliationRegistry,
    reconcile_account_equity,
    reconcile_source_events,
    require_reconciled,
    require_source_events_reconciled,
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
    ledger = _ledger(tmp_path / "equity.sqlite")
    result = reconcile_account_equity(
        ledger=ledger, account_id=ACCOUNT,
        opening_as_of=START, closing_as_of=END, reported_net_pnl_inr=Decimal("90"),
    )
    assert result.opening_equity_inr == Decimal("100000.00")
    assert result.closing_equity_inr == Decimal("100590.00")
    assert result.external_flows_inr == Decimal("500.00")
    assert result.net_investment_pnl_inr == Decimal("90.00")
    assert result.reported_net_pnl_inr == Decimal("90")
    assert result.difference_inr == Decimal("0.00")
    assert result.reconciled
    assert result.event_count == 7
    assert result.event_sha256 and result.opening_snapshot_sha256 and result.closing_snapshot_sha256
    assert len(result.report_sha256) == 64
    assert result.registry_status == "CURRENT"
    require_reconciled(result)
    registry = EquityReconciliationRegistry(tmp_path / "equity.sqlite")
    assert registry.status(report_sha256=result.report_sha256)["status"] == "CURRENT"


def test_unsettled_cash_receivable_remains_part_of_reconciled_equity(tmp_path):
    ledger = _ledger(tmp_path / "receivable-equity.sqlite")
    ledger.append(_event(
        "redemption-receivable", EventKind.CASH_RECEIVABLE, START + timedelta(hours=3),
        cash_delta="-200", metadata={"action": "RECOGNIZE", "amount": "200",
                                      "receivable_id": "mf-redemption-1"},
    ))

    report = reconcile_account_equity(
        ledger=ledger, account_id=ACCOUNT, opening_as_of=START, closing_as_of=END,
        reported_net_pnl_inr=Decimal("90"),
    )

    assert report.closing_equity_inr == Decimal("100590.00")
    assert report.net_investment_pnl_inr == Decimal("90.00")
    assert report.difference_inr == Decimal("0.00")
    assert report.reconciled

    # Payout converts the recognized claim into cash without changing account equity.
    ledger.append(_event(
        "redemption-payout", EventKind.CASH_RECEIVABLE, END + timedelta(minutes=1),
        cash_delta="200", evidence_class="OBSERVED_PAYOUT_EVIDENCE",
        metadata={"action": "SETTLE", "amount": "200", "receivable_id": "mf-redemption-1",
                  "source_id": "rta-statement", "source_reference": "payout-1"},
    ))
    settled = ledger.snapshot(ACCOUNT, as_of=(END + timedelta(minutes=1)).isoformat())
    assert settled["cash"]["INR"] == Decimal("99490")
    assert settled["cash"]["INR"] + Decimal("10") * Decimal("110") == Decimal("100590")
    assert settled["cash_receivables"] == []


def test_source_statement_rows_match_canonical_source_event_set(tmp_path):
    ledger = _ledger(tmp_path / "source.db")
    # Construct an independently supplied statement fixture instead of reading rows back
    # from the canonical ledger under test.
    source_rows = [
        _event("external", EventKind.CASH_FLOW, START + timedelta(hours=1), cash_delta="500",
               metadata={"flow_type": "EXTERNAL", "flow_id": "deposit-1"}),
        _event("dividend", EventKind.CASH_FLOW, START + timedelta(hours=2), cash_delta="10",
               metadata={"flow_type": "DISTRIBUTION", "source": "issuer"}),
        _event("fee", EventKind.FEE, START + timedelta(hours=2), cash_delta="-20"),
        _event("mark-close", EventKind.MARK, END, asset_class=AssetClass.EQUITY,
               instrument_id="NSE:ABC", price="110"),
    ]

    report = reconcile_source_events(
        ledger=ledger, account_id=ACCOUNT, source_system="paper-test",
        period_start=START, period_end=END, source_events=source_rows,
    )

    assert report.reconciled
    assert report.canonical_event_count == report.source_event_count == report.matched_event_count == 4
    assert report.canonical_events_sha256 == report.source_events_sha256
    assert len(report.report_sha256) == 64
    assert "not authenticated" in report.limitations[0]
    assert report.registry_status == "CURRENT"
    require_source_events_reconciled(report)


def test_source_statement_comparison_reports_missing_extra_and_changed_rows(tmp_path):
    ledger = _ledger(tmp_path / "diff.db")
    source_rows = [_event("external-only", EventKind.CASH_FLOW, START + timedelta(hours=4),
                          cash_delta="25")]
    source_rows.append(_event("fee", EventKind.FEE, START + timedelta(hours=2), cash_delta="-19"))
    report = reconcile_source_events(
        ledger=ledger, account_id=ACCOUNT, source_system="paper-test",
        period_start=START, period_end=END, source_events=source_rows,
    )

    assert not report.reconciled
    assert report.statement_only_event_ids == ("external-only",)
    assert report.ledger_only_event_ids == ("dividend", "external", "mark-close")
    assert report.mismatched_event_ids == ("fee",)


def test_source_statement_comparison_rejects_duplicate_source_event_ids(tmp_path):
    ledger = _ledger(tmp_path / "dupe.db")
    duplicate = _event("same-ref", EventKind.CASH_FLOW, START + timedelta(hours=1), cash_delta="1")
    with pytest.raises(EquityReconciliationError, match="duplicate source event"):
        reconcile_source_events(
            ledger=ledger, account_id=ACCOUNT, source_system="paper-test",
            period_start=START, period_end=END, source_events=[duplicate, duplicate],
        )


def test_source_event_receipt_invalidates_on_late_backdated_canonical_event(tmp_path):
    ledger = _ledger(tmp_path / "late.db")
    source_rows = [
        _event("external", EventKind.CASH_FLOW, START + timedelta(hours=1), cash_delta="500",
               metadata={"flow_type": "EXTERNAL", "flow_id": "deposit-1"}),
        _event("dividend", EventKind.CASH_FLOW, START + timedelta(hours=2), cash_delta="10",
               metadata={"flow_type": "DISTRIBUTION", "source": "issuer"}),
        _event("fee", EventKind.FEE, START + timedelta(hours=2), cash_delta="-20"),
        _event("mark-close", EventKind.MARK, END, asset_class=AssetClass.EQUITY,
               instrument_id="NSE:ABC", price="110"),
    ]
    report = reconcile_source_events(
        ledger=ledger, account_id=ACCOUNT, source_system="paper-test",
        period_start=START, period_end=END, source_events=source_rows,
    )
    ledger.append(_event("late-source-event", EventKind.CASH_FLOW,
                         START + timedelta(hours=4), cash_delta="1"))

    registry = EquityReconciliationRegistry(ledger.path)
    assert registry.source_event_status(report_sha256=report.report_sha256) == {
        "status": "INVALIDATED",
        "invalidation_reason": "CANONICAL_SOURCE_EVENTS_CHANGED_THROUGH_PERIOD_END",
    }
    with pytest.raises(EquityReconciliationError, match="not current"):
        require_source_events_reconciled(report)


def test_source_event_receipt_ignores_post_period_canonical_events(tmp_path):
    ledger = _ledger(tmp_path / "after.db")
    source_rows = [
        _event("external", EventKind.CASH_FLOW, START + timedelta(hours=1), cash_delta="500",
               metadata={"flow_type": "EXTERNAL", "flow_id": "deposit-1"}),
        _event("dividend", EventKind.CASH_FLOW, START + timedelta(hours=2), cash_delta="10",
               metadata={"flow_type": "DISTRIBUTION", "source": "issuer"}),
        _event("fee", EventKind.FEE, START + timedelta(hours=2), cash_delta="-20"),
        _event("mark-close", EventKind.MARK, END, asset_class=AssetClass.EQUITY,
               instrument_id="NSE:ABC", price="110"),
    ]
    report = reconcile_source_events(
        ledger=ledger, account_id=ACCOUNT, source_system="paper-test",
        period_start=START, period_end=END, source_events=source_rows,
    )
    ledger.append(_event("future-source-event", EventKind.CASH_FLOW,
                         END + timedelta(seconds=1), cash_delta="1"))

    assert EquityReconciliationRegistry(ledger.path).source_event_status(
        report_sha256=report.report_sha256,
    ) == {"status": "CURRENT", "invalidation_reason": None}
    require_source_events_reconciled(report)


def test_missing_opening_mark_fails_closed(tmp_path):
    result = reconcile_account_equity(
        ledger=_ledger(tmp_path / "equity.sqlite", mark_at_open=False), account_id=ACCOUNT,
        opening_as_of=START, closing_as_of=END, reported_net_pnl_inr=Decimal("90"),
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
                                 opening_as_of=START.replace(tzinfo=None), closing_as_of=END,
                                 reported_net_pnl_inr=Decimal("90"))
    with pytest.raises(EquityReconciliationError, match="increasing reconciliation interval"):
        reconcile_account_equity(ledger=ledger, account_id=ACCOUNT,
                                 opening_as_of=END, closing_as_of=START,
                                 reported_net_pnl_inr=Decimal("90"))


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
                                      opening_as_of=START, closing_as_of=END,
                                      reported_net_pnl_inr=Decimal("90"))
    assert not result.reconciled
    assert result.difference_inr is None
    assert any(blocker.startswith("LEDGER_UNCERTAIN") for blocker in result.blockers)


def test_independent_pnl_mismatch_beyond_paise_tolerance_is_not_reconciled(tmp_path):
    result = reconcile_account_equity(
        ledger=_ledger(tmp_path / "equity.sqlite"), account_id=ACCOUNT,
        opening_as_of=START, closing_as_of=END,
        reported_net_pnl_inr=Decimal("90.02"),
    )
    assert result.net_investment_pnl_inr == Decimal("90.00")
    assert result.difference_inr == Decimal("0.02")
    assert not result.reconciled


def test_late_backdated_event_invalidates_persisted_reconciliation_report(tmp_path):
    ledger = _ledger(tmp_path / "equity.sqlite")
    report = reconcile_account_equity(
        ledger=ledger, account_id=ACCOUNT, opening_as_of=START, closing_as_of=END,
        reported_net_pnl_inr=Decimal("90"),
    )
    registry = EquityReconciliationRegistry(tmp_path / "equity.sqlite")
    assert registry.status(report_sha256=report.report_sha256)["status"] == "CURRENT"

    ledger.append(_event("late-backdated-distribution", EventKind.CASH_FLOW,
                         START + timedelta(hours=3), cash_delta="5",
                         metadata={"flow_type": "DISTRIBUTION", "source": "late-arrival"}))

    status = registry.status(report_sha256=report.report_sha256)
    assert status == {
        "status": "INVALIDATED",
        "invalidation_reason": "CANONICAL_EVENTS_CHANGED_THROUGH_CLOSING_CUTOFF",
    }
    with pytest.raises(EquityReconciliationError, match="not current"):
        require_reconciled(report)


def test_post_cutoff_event_does_not_invalidate_historical_reconciliation(tmp_path):
    ledger = _ledger(tmp_path / "equity.sqlite")
    report = reconcile_account_equity(
        ledger=ledger, account_id=ACCOUNT, opening_as_of=START, closing_as_of=END,
        reported_net_pnl_inr=Decimal("90"),
    )
    registry = EquityReconciliationRegistry(tmp_path / "equity.sqlite")

    ledger.append(_event("later-distribution", EventKind.CASH_FLOW,
                         END + timedelta(minutes=1), cash_delta="5",
                         metadata={"flow_type": "DISTRIBUTION", "source": "later-session"}))

    assert registry.status(report_sha256=report.report_sha256)["status"] == "CURRENT"


def test_concurrent_append_waits_until_open_close_snapshots_share_one_cutoff(tmp_path):
    ledger = _ledger(tmp_path / "equity.sqlite")
    original_events = ledger.events
    writer_started = Event()
    writer_finished = Event()

    def append_event() -> None:
        writer_started.set()
        ledger.append(_event("late-distribution", EventKind.CASH_FLOW, END,
                             cash_delta="25", metadata={"flow_type": "DISTRIBUTION"}))
        writer_finished.set()

    writer = Thread(target=append_event)

    def events_then_start_writer(account_id, *, as_of=None):
        rows = original_events(account_id, as_of=as_of)
        if not writer_started.is_set():
            writer.start()
            assert writer_started.wait(timeout=2)
            # The reconciliation's reserved SQLite write lock prevents a concurrent
            # append from splitting its event-set hash and opening/closing snapshots.
            assert not writer_finished.wait(timeout=0.05)
        return rows

    ledger.events = events_then_start_writer
    result = reconcile_account_equity(
        ledger=ledger, account_id=ACCOUNT, opening_as_of=START, closing_as_of=END,
        reported_net_pnl_inr=Decimal("90"),
    )
    writer.join(timeout=5)
    assert not writer.is_alive()
    assert result.reconciled
    assert result.event_count == 7
    invalidated = EquityReconciliationRegistry(tmp_path / "equity.sqlite").status(
        report_sha256=result.report_sha256)
    assert invalidated["status"] == "INVALIDATED"
    with pytest.raises(EquityReconciliationError, match="not current"):
        require_reconciled(result)
    assert ledger.snapshot(ACCOUNT, as_of=END.isoformat())["cash"]["INR"] == Decimal("99515")
