from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.forward_edge.trader_dashboard import (
    DESKS,
    DeskDashboardSnapshot,
    DashboardHolding,
    TraderDashboardError,
    build_trader_dashboard,
    render_trader_dashboard,
)


NOW = datetime(2026, 11, 27, 6, tzinfo=timezone.utc)


def _snapshots(**changes):
    rows = []
    for index, desk in enumerate(sorted(DESKS)):
        values = dict(
            desk=desk, as_of=NOW - timedelta(seconds=index * 2),
            source_mode="FIXTURE" if desk == "OPTIONS" else "OBSERVED_MARKET_DATA",
            source_sha256=f"{index + 1:064x}", newest_quote_age_seconds=1.0 + index,
            maximum_quote_age_seconds=10.0, risk_policy_status="PASS",
            holdings=(DashboardHolding("NIFTY-TEST", 1, 1000, 20),),
            free_cash_inr=50000, daily_net_pnl_inr=125,
            historical_net_pnl_inr=1000)
        values.update(changes.get(desk, {}))
        rows.append(DeskDashboardSnapshot(**values))
    return tuple(rows)


def test_five_desk_snapshot_exposes_source_freshness_risk_cash_holdings_and_pnl(tmp_path):
    report = build_trader_dashboard(snapshots=_snapshots())
    assert report["desk_count"] == 5
    assert report["synchronized"] is True
    assert report["global_risk_status"] == "PASS"
    by_desk = {row["desk"]: row for row in report["desks"]}
    assert by_desk["OPTIONS"]["source_mode"] == "FIXTURE"
    assert by_desk["SWING"]["source_sha256"] == f"{5:064x}"
    assert by_desk["SWING"]["holdings"][0]["instrument"] == "NIFTY-TEST"
    assert by_desk["SWING"]["free_cash_inr"] == 50000
    assert by_desk["SWING"]["daily_net_pnl_inr"] == 125
    assert len(report["snapshot_sha256"]) == 64
    destination = render_trader_dashboard(report, tmp_path / "dashboard.html")
    page = destination.read_text(encoding="utf-8")
    assert "Five-desk paper trader dashboard" in page
    assert "OBSERVED_MARKET_DATA" in page and "FIXTURE" in page
    assert "physical orders disabled" in page


def test_missing_stale_or_unsynchronized_desk_evidence_blocks_readiness():
    with pytest.raises(TraderDashboardError, match="exactly one snapshot"):
        build_trader_dashboard(snapshots=_snapshots()[:-1])
    stale = build_trader_dashboard(snapshots=_snapshots(OPTIONS={"newest_quote_age_seconds": 60}))
    assert stale["global_risk_status"] == "BLOCK"
    delayed = build_trader_dashboard(snapshots=_snapshots(
        MUTUAL_FUND={"as_of": NOW - timedelta(minutes=5)}))
    assert delayed["synchronized"] is False
    assert delayed["global_risk_status"] == "BLOCK"


def test_unknown_source_mode_and_nonfinite_cash_are_refused():
    with pytest.raises(TraderDashboardError, match="source mode"):
        build_trader_dashboard(snapshots=_snapshots(SWING={"source_mode": "LIVE"}))
    with pytest.raises(TraderDashboardError, match="finite"):
        build_trader_dashboard(snapshots=_snapshots(SWING={"free_cash_inr": float("nan")}))
