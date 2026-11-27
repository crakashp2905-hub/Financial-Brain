from __future__ import annotations

from datetime import date

import pytest

from financial_brain.forward_edge.account_period_report import (
    AccountPeriodReportError,
    AccountSessionSnapshot,
    SessionContribution,
    build_account_period_report,
)


def _contribution(strategy, desk, instrument, realized, unrealized, trade_count=1):
    return SessionContribution(strategy, desk, instrument, realized, unrealized,
                               realized + unrealized, trade_count)


def _snapshots():
    return (
        AccountSessionSnapshot(date(2026, 10, 30), 1000, 1010, 800, 210,
                               10, 0, 0, (_contribution("momentum", "equity", "INFY", 10, 0),)),
        AccountSessionSnapshot(date(2026, 11, 2), 1010, 1005, 900, 105,
                               10, -5, 0, (_contribution("swing", "equity", "TCS", 0, -5),)),
        AccountSessionSnapshot(date(2026, 11, 3), 1005, 1015, 910, 105,
                               20, -5, 0, (_contribution("momentum", "equity", "INFY", 10, 0),)),
    )


def test_account_report_reconciles_cash_mtd_cumulative_and_contribution_views():
    report = build_account_period_report(snapshots=_snapshots())
    assert report["cash_position_reconciled"] is True
    assert report["available_cash_inr"] == 910
    assert report["realized_pnl_inr"] == 20
    assert report["unrealized_pnl_inr"] == -5
    assert report["month_to_date"]["net_pnl_inr"] == 5
    assert report["month_to_date"]["realized_pnl_inr"] == 10
    assert report["month_to_date"]["unrealized_pnl_change_inr"] == -5
    assert report["month_to_date"]["contributions"]["strategy"]["momentum"]["net_pnl_inr"] == 10
    assert report["cumulative"]["net_pnl_inr"] == 15
    assert report["cumulative"]["contributions"]["instrument"]["TCS"]["net_pnl_inr"] == -5
    assert len(report["report_sha256"]) == 64


def test_equity_cash_and_attribution_must_reconcile_before_report():
    bad_cash = list(_snapshots())
    bad_cash[-1] = AccountSessionSnapshot(date(2026, 11, 3), 1005, 1015, 900, 105,
                                           20, -5, 0,
                                           (_contribution("momentum", "equity", "INFY", 10, 0),))
    with pytest.raises(AccountPeriodReportError, match="cash and marked positions"):
        build_account_period_report(snapshots=tuple(bad_cash))
    bad_pnl = list(_snapshots())
    bad_pnl[-1] = AccountSessionSnapshot(date(2026, 11, 3), 1005, 1015, 910, 105,
                                          20, -5, 0,
                                          (_contribution("momentum", "equity", "INFY", 9, 0),))
    with pytest.raises(AccountPeriodReportError, match="session contributions"):
        build_account_period_report(snapshots=tuple(bad_pnl))
