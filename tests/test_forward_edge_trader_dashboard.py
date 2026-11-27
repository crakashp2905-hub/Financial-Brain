from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.forward_edge.trader_dashboard import (
    DESKS,
    DeskDashboardSnapshot,
    DashboardHolding,
    DashboardDecision,
    DirectionForecast,
    AlertCondition,
    OperationalIssue,
    PerformanceAttribution,
    ResearchHypothesis,
    ReplayCandle,
    ReplayDecision,
    TraceEvent,
    TraderDashboardError,
    build_trader_dashboard,
    render_trader_dashboard,
    _trace_digest,
    derive_conservative_alerts,
    build_operator_replay,
    render_operator_replay,
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


def test_dashboard_separates_forecast_actions_no_trade_and_option_execution_evidence(tmp_path):
    forecast = DirectionForecast(NOW, .5, .3, .2, 24000, 24200, "VALID", "CALIBRATED")
    no_trade = DashboardDecision("d1", "NIFTY", "NO_TRADE", NOW, "spread gate blocked",
                                 blocked_reason="spread too wide", expected_cost_inr=25,
                                 fee_uncertainty_inr=5)
    option = DashboardDecision("d2", "NIFTY26DEC24000CE", "WATCH", NOW, "candidate only",
                               option_expiry="2026-12-31", option_strike=24000, option_right="CE",
                               option_lot_size=25, bid=100, ask=101, quote_source="KITE_PAPER",
                               quote_at=NOW, maximum_quote_age_seconds=5,
                               sl_policy_trigger=80, tp_policy_trigger=140)
    report = build_trader_dashboard(snapshots=_snapshots(), forecast=forecast,
                                    decisions=(no_trade, option))
    page = render_trader_dashboard(report, tmp_path / "decisions.html").read_text(encoding="utf-8")
    assert "Market direction forecast (not an order)" in page
    assert "NO_TRADE" in page and "spread too wide" in page
    assert "2026-12-31" in page and "KITE_PAPER" in page
    assert "Quote age seconds" in page
    assert "SL/TP values are policy triggers" in page
    assert report["forecast"]["p_flat"] == .2
    assert report["decisions"][1]["actual_exit_price"] is None
    assert report["decisions"][1]["quote_age_seconds"] == 0
    assert report["decisions"][1]["option_quote_status"] == "FRESH"
    assert report["decisions"][1]["order_status"] == "NO_ORDER_PLACED"


def test_dashboard_refuses_invalid_forecast_or_unidentified_option_quote():
    with pytest.raises(TraderDashboardError, match="sum to one"):
        build_trader_dashboard(snapshots=_snapshots(), forecast=DirectionForecast(
            NOW, .6, .3, .2, 24000, 24200, "VALID", "CALIBRATED"))
    quote = DashboardDecision("d1", "NIFTYCE", "WATCH", NOW, "observe", bid=100, ask=101)
    with pytest.raises(TraderDashboardError, match="quote needs"):
        build_trader_dashboard(snapshots=_snapshots(), decisions=(quote,))


def test_dashboard_reconciles_performance_and_renders_red_alerts_and_immutable_trace(tmp_path):
    signal = TraceEvent("s1", "SIGNAL", NOW, "a" * 64)
    order = TraceEvent("o1", "ORDER", NOW + timedelta(seconds=1), "b" * 64,
                       _trace_digest(signal))
    attribution = PerformanceAttribution("momentum-v2", "INTRADAY", 125, 25, 100, .012, "PAPER_SHADOW")
    issue = OperationalIssue("MISSING_EXIT", "OPEN", "position exit not reconciled", "c" * 64)
    report = build_trader_dashboard(
        snapshots=_snapshots(), attribution=(attribution,), operational_issues=(issue,),
        trace=(signal, order), past_snapshot_sha256="d" * 64)
    page = render_trader_dashboard(report, tmp_path / "ops.html").read_text(encoding="utf-8")
    assert "Strategy and desk performance" in page and "PAPER_SHADOW" in page
    assert "RED OPERATIONAL ALERTS" in page and "MISSING_EXIT" in page
    assert "Signal → order → fill → exit trace" in page and "d" * 64 in page
    assert len(report["trace"][1]["event_sha256"]) == 64


def test_dashboard_refuses_unreconciled_performance_and_broken_trace_chain():
    with pytest.raises(TraderDashboardError, match="reconcile"):
        build_trader_dashboard(snapshots=_snapshots(), attribution=(PerformanceAttribution(
            "momentum-v2", "INTRADAY", 125, 20, 100, .01, "PAPER"),))
    signal = TraceEvent("s1", "SIGNAL", NOW, "a" * 64)
    bad_order = TraceEvent("o1", "ORDER", NOW + timedelta(seconds=1), "b" * 64, "f" * 64)
    with pytest.raises(TraderDashboardError, match="hash-linked"):
        build_trader_dashboard(snapshots=_snapshots(), trace=(signal, bad_order))


def test_conservative_alerts_report_new_conditions_once_and_never_place_orders(tmp_path):
    previous = (AlertCondition("feed", "DATA_HEALTH_FAILURE", True, "feed stale"),
                AlertCondition("stop:p1", "SL_TRIGGER", False, "stop policy crossed"))
    current = (AlertCondition("feed", "DATA_HEALTH_FAILURE", True, "feed stale"),
               AlertCondition("stop:p1", "SL_TRIGGER", True, "stop policy crossed"),
               AlertCondition("signal:s1", "ELIGIBLE_SIGNAL", True, "new paper candidate"),
               AlertCondition("kill", "KILL_SWITCH", True, "risk kill switch latched"))
    alerts = derive_conservative_alerts(previous=previous, current=current)
    assert {row["kind"] for row in alerts} == {"SL_TRIGGER", "ELIGIBLE_SIGNAL", "KILL_SWITCH"}
    assert all(row["action"] == "OPERATOR_REVIEW_ONLY" for row in alerts)
    report = build_trader_dashboard(snapshots=_snapshots(), alerts=alerts)
    page = render_trader_dashboard(report, tmp_path / "alerts.html").read_text(encoding="utf-8")
    assert "Conservative alerts" in page and "no order action" in page


def test_research_board_and_replay_preserve_version_history_and_point_in_time_visibility(tmp_path):
    hypothesis = ResearchHypothesis("breakout", "2.1.0", "HISTORICAL_INCONCLUSIVE", "FORWARD_SHADOW",
                                    ("breakout-v1",), "Not enough independent forward sessions")
    report = build_trader_dashboard(snapshots=_snapshots(), hypotheses=(hypothesis,))
    page = render_trader_dashboard(report, tmp_path / "board.html").read_text(encoding="utf-8")
    assert "Research hypothesis board" in page and "breakout-v1" in page
    assert "Not enough independent forward sessions" in page

    first = ReplayCandle(NOW, NOW + timedelta(seconds=2), 100, 103, 99, 102)
    second = ReplayCandle(NOW + timedelta(seconds=10), NOW + timedelta(seconds=12), 102, 105, 101, 104)
    decision = ReplayDecision(NOW + timedelta(seconds=5), "d1", "WATCH", "candidate only")
    replay = build_operator_replay(session_id="s-1", frame_times=(NOW + timedelta(seconds=6),
                                      NOW + timedelta(seconds=15)), candles=(first, second), decisions=(decision,))
    frames = replay["frames"]
    assert len(frames[0]["candles"]) == 1 and len(frames[0]["decisions"]) == 1
    assert len(frames[1]["candles"]) == 2
    replay_html = render_operator_replay(replay, tmp_path / "replay.html").read_text(encoding="utf-8")
    assert "type='range'" in replay_html and "Play" in replay_html
    assert "available by the selected time" in replay_html


def test_replay_rejects_naive_time_and_never_exposes_future_candle_early():
    candle = ReplayCandle(NOW, NOW + timedelta(seconds=10), 100, 101, 99, 100)
    replay = build_operator_replay(session_id="s-1", frame_times=(NOW + timedelta(seconds=2),),
                                   candles=(candle,), decisions=())
    assert replay["frames"][0]["candles"] == []
    with pytest.raises(TraderDashboardError, match="timezone-aware"):
        build_operator_replay(session_id="s-1", frame_times=(datetime(2026, 11, 27),),
                              candles=(), decisions=())


def test_usability_false_positive_buy_and_stale_option_cannot_look_executable():
    stock_buy = DashboardDecision("stock-buy", "RELIANCE", "BUY", NOW, "model candidate")
    stale_option = DashboardDecision(
        "option-buy", "NIFTY26DEC24000CE", "BUY", NOW, "model candidate",
        option_expiry="2026-12-31", option_strike=24000, option_right="CE", option_lot_size=25,
        bid=100, ask=101, quote_source="KITE_PAPER", quote_at=NOW - timedelta(seconds=30),
        maximum_quote_age_seconds=5)
    report = build_trader_dashboard(snapshots=_snapshots(), decisions=(stock_buy, stale_option))
    assert report["global_risk_status"] == "BLOCK"
    decisions = {row["decision_id"]: row for row in report["decisions"]}
    assert decisions["stock-buy"]["position_status"] == "NO_POSITION_STATE_RECORDED"
    assert decisions["stock-buy"]["order_status"] == "NO_ORDER_PLACED"
    assert decisions["option-buy"]["option_quote_status"] == "STALE_OR_UNKNOWN"
    with pytest.raises(TraderDashboardError, match="future"):
        build_trader_dashboard(snapshots=_snapshots(), decisions=(DashboardDecision(
            "future-option", "NIFTYCE", "WATCH", NOW, "candidate", option_expiry="2026-12-31",
            option_strike=24000, option_right="CE", option_lot_size=25, bid=100, ask=101,
            quote_source="KITE_PAPER", quote_at=NOW + timedelta(seconds=1), maximum_quote_age_seconds=5),))
