"""F10 - trader cockpit (FE-0114 .. FE-0123). Static read-only pages; nothing here can submit an order."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal as D

import pytest

from financial_brain.forward_edge import cockpit as C
from financial_brain.forward_edge.registry import Status


def test_landing_page_says_at_once_whether_the_run_is_trustworthy() -> None:
    good = C.landing(feed_ok=True, broker="DISABLED", market_status="OPEN", risk_gates_clear=True, mode="LIVE_PAPER",
                     tier="LOCAL_PAPER", reasons=[])
    assert ">TRUSTWORTHY<" in good and "Mode: LIVE_PAPER" in good
    bad = C.landing(feed_ok=False, broker="DISABLED", market_status="OPEN", risk_gates_clear=True, mode="LIVE_PAPER",
                    tier="LOCAL_PAPER", reasons=["STALE_QUOTE"])
    assert ">NOT TRUSTWORTHY<" in bad and "STALE_QUOTE" in bad
    enabled = C.landing(feed_ok=True, broker="ENABLED", market_status="OPEN", risk_gates_clear=True, mode="LIVE_PAPER",
                        tier="LOCAL_PAPER", reasons=[])
    assert ">NOT TRUSTWORTHY<" in enabled


def _overview_inputs():
    now = datetime(2026, 11, 14, 4, 0, tzinfo=timezone.utc)
    return {
        "now": now,
        "feed": {"source": "kite", "exchange": "NSE", "complete": True,
                 "last_received_utc": now.isoformat(), "max_age_seconds": 5},
        "calendar": {"is_session": True, "version": "nse-2026.11", "evidence_sha256": "a" * 64,
                     "checked_at_utc": now.isoformat(), "max_age_seconds": 3600},
        "active_version": {"strategy_id": "momentum", "version": "3", "artifact_sha256": "b" * 64},
        "broker": {"read_only": True, "session_id": "kite-readonly-1", "evidence_sha256": "c" * 64,
                   "observed_at_utc": now.isoformat(), "max_age_seconds": 60},
        "risk_latches": [],
    }


def test_session_overview_requires_fresh_complete_evidence_and_clear_latches():
    inputs = _overview_inputs()
    ready = C.session_overview(**inputs)
    assert "PAPER SESSION READY" in ready
    assert "Feed" in ready and "Market calendar" in ready and "Broker session" in ready

    inputs["feed"] = {**inputs["feed"], "complete": False,
                       "last_received_utc": "2026-11-14T03:59:00+00:00"}
    inputs["risk_latches"] = ["MAX_DRAWDOWN"]
    blocked = C.session_overview(**inputs)
    assert "NOT READY — TRADING BLOCKED" in blocked
    assert "FEED_INCOMPLETE" in blocked and "FEED_STALE" in blocked
    assert "RISK_LATCH:MAX_DRAWDOWN" in blocked


def test_session_overview_never_calls_missing_sources_ready():
    inputs = _overview_inputs()
    inputs["calendar"] = {"is_session": True, "version": "", "evidence_sha256": "",
                           "checked_at_utc": None, "max_age_seconds": 60}
    inputs["active_version"] = None
    blocked = C.session_overview(**inputs)
    assert "NOT READY — TRADING BLOCKED" in blocked
    assert "CALENDAR_EVIDENCE_MISSING_OR_INVALID" in blocked
    assert "ACTIVE_STRATEGY_VERSION_MISSING_OR_INVALID" in blocked


def test_failed_strategies_are_visually_distinct_from_promotable_candidates() -> None:
    html = C.strategy_table([
        {"strategy_id": "M1", "family": "mom", "version": 2, "status": Status.FAILED, "tier": "HISTORICAL"},
        {"strategy_id": "ORB", "family": "orb", "version": 1, "status": Status.FROZEN_CHALLENGER, "tier": "LOCAL_PAPER"},
        {"strategy_id": "X", "family": "x", "version": 1, "status": Status.SHADOW_ONLY, "tier": "SHADOW"},
    ])
    assert "class='failed'><td>M1" in html and "class='promotable'><td>ORB" in html and "class=''><td>X" in html


def test_signal_panel_traces_to_the_snapshot_and_needs_every_field() -> None:
    sig = {"snapshot_sha256": "a" * 64, "intended_order": "BUY 10 INFY", "spread_bps": 4, "volume": 1000,
           "limits": "band 1400-1600", "expiry": "n/a", "reason_codes": "BREAKOUT"}
    assert "a" * 64 in C.signal_panel(sig)
    with pytest.raises(C.CockpitError, match="missing"):
        C.signal_panel({k: v for k, v in sig.items() if k != "snapshot_sha256"})


def test_unknown_and_unfilled_orders_never_appear_as_trades() -> None:
    html = C.blotter([{"id": "o1", "state": "FILLED", "filled": 10}, {"id": "o2", "state": "UNKNOWN"},
                      {"id": "o3", "state": "SUBMITTED"}, {"id": "o4", "state": "PARTIAL", "filled": 3}])
    trades = html.split("id='trades'>")[1]
    assert "o1" in trades and "o4" in trades and "o2" not in trades and "o3" not in trades
    with pytest.raises(C.CockpitError):
        C.blotter([{"id": "o9", "state": "ACKED"}])


def test_pnl_table_reconciles_to_the_ledger_exactly_or_refuses() -> None:
    html = C.pnl_table(D("1000"), D("120"), D("30"), D("850"), D("400"), D("850"))
    assert "Net alpha" in html and ">450<" in html
    with pytest.raises(C.CockpitError, match="gross minus"):
        C.pnl_table(D("1000"), D("120"), D("30"), D("860"), D("400"), D("860"))
    with pytest.raises(C.CockpitError, match="ledger"):
        C.pnl_table(D("1000"), D("120"), D("30"), D("850"), D("400"), D("851"))


def test_timeline_lists_every_scheduled_session_including_empty_ones() -> None:
    html = C.timeline(["d1", "d2", "d3"], {"d1": "SUBMITTED", "d2": "NO_SIGNAL"})
    assert "Scheduled: 3" in html and "d2" in html and "UNRECORDED" in html


def test_scorecard_refuses_to_rank_different_horizons() -> None:
    with pytest.raises(C.CockpitError, match="NOT_COMPARABLE"):
        C.scorecard([{"strategy_id": "A", "horizon_sessions": 5, "net_alpha_lcb": 0.1, "capacity": "1cr"},
                     {"strategy_id": "B", "horizon_sessions": 250, "net_alpha_lcb": 0.2, "capacity": "2cr"}])
    html = C.scorecard([{"strategy_id": "A", "horizon_sessions": 20, "net_alpha_lcb": 0.1, "capacity": "1cr"},
                        {"strategy_id": "B", "horizon_sessions": 20, "net_alpha_lcb": 0.2, "capacity": "2cr"}])
    assert html.index(">B<") < html.index(">A<")


def test_evidence_links_show_hashes() -> None:
    assert "b" * 64 in C.evidence_links({"manifests.jsonl": "b" * 64})


@pytest.mark.parametrize("bad", ["<form action='/x'>", "<button>Buy</button>", "<input name=q>", "<a onclick='x()'>",
                                 "<script>fetch('/order')</script>", "<div method=post>"])
def test_any_action_element_is_refused_so_viewing_never_submits(bad: str) -> None:
    with pytest.raises(C.CockpitError):
        C.assert_read_only("<html>" + bad + "</html>")


def test_every_page_generator_passes_the_read_only_check() -> None:
    pages = [C.landing(feed_ok=True, broker="DISABLED", market_status="OPEN", risk_gates_clear=True, mode="SHADOW",
                       tier="SHADOW", reasons=[]), C.decisions_panel(["NO_INDEPENDENT_CUSTODY"]),
             C.timeline(["d1"], {}), C.evidence_links({})]
    for p in pages:
        C.assert_read_only(p)


def test_operator_decisions_are_the_five_simple_options_and_explain_the_live_lock() -> None:
    html = C.decisions_panel(["INDEPENDENT_TIMESTAMP_RECEIPTS_NOT_CONFIGURED", "BROKER_REHEARSALS_NOT_COMPLETE"])
    for d in ("CONTINUE_PAPER", "PAUSE", "INVESTIGATE", "REJECT", "REQUEST_REVIEW"):
        assert d in html
    assert "LOCKED" in html and "INDEPENDENT_TIMESTAMP_RECEIPTS_NOT_CONFIGURED" in html
