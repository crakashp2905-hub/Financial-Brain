from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
import json
import statistics
import time
import urllib.request

import pytest

from financial_brain.forward_edge.performance_report import (
    PerformanceReportError,
    PerformanceSession,
    PerformanceTrade,
    build_performance_report,
)


def _sessions():
    return (
        PerformanceSession(date(2026, 10, 29), 0.02, 1000, 10000, 0.8, 0.5, 3000),
        PerformanceSession(date(2026, 10, 30), -0.03, 1500, 10200, 0.9, 0.4, 3500),
        PerformanceSession(date(2026, 11, 2), -0.01, 500, 9900, 0.6, -0.2, 2500),
    )


def test_report_covers_cost_aware_returns_drawdown_periods_trade_bp_and_capacity():
    report = build_performance_report(
        sessions=_sessions(),
        trades=(PerformanceTrade(10, 1000), PerformanceTrade(-20, 2000)),
        risk_free_daily=0.0001)
    assert report["worst_day_net_return"] == -0.03
    assert report["worst_week_net_return"] == pytest.approx((1.02 * 0.97) - 1)
    assert report["worst_month_net_return"] == pytest.approx((1.02 * 0.97) - 1)
    assert report["longest_consecutive_loss_days"] == 2
    assert report["mean_net_basis_points_per_trade"] == pytest.approx(0.0)
    assert report["notional_weighted_net_basis_points_per_trade"] == pytest.approx(-33.3333333333)
    assert report["capacity_utilization"] == pytest.approx(3000 / 9000)
    assert report["annualized_sharpe"] is not None
    assert report["annualized_sortino"] is not None
    assert report["confidence_intervals"] == "NOT_COMPUTED_USE_STATIONARY_BLOCK_RISK_REPORT"
    assert len(report["report_sha256"]) == 64


def test_concurrent_forecast_options_and_protective_exit_load_with_broker_denied(monkeypatch):
    """Local synthetic workload smoke benchmark; no production SLA is implied."""
    from financial_brain.execution.oms.kite_broker import KiteBroker
    from financial_brain.execution import order_policy
    from financial_brain.forecasting.index_15m import features_at, random_walk_baseline
    from financial_brain.forecasting.index_market import IndexBar15m
    from financial_brain.paper.options import Quote
    from financial_brain.paper.options_exit import OptionExitQuote, PremiumExitPolicy, manage_long_option_exit
    from financial_brain.paper.options_spread import Leg, debit_vertical

    calls = []
    def denied(*_args, **_kwargs):
        calls.append("broker-or-network")
        raise AssertionError("load workload attempted broker/network access")
    monkeypatch.setattr(KiteBroker, "place", denied)
    monkeypatch.setattr(KiteBroker, "cancel", denied)
    monkeypatch.setattr(KiteBroker, "_request", denied)
    monkeypatch.setattr(urllib.request, "urlopen", denied)
    monkeypatch.setattr(order_policy, "PHYSICAL_ORDERS_ENABLED", True)

    from zoneinfo import ZoneInfo
    ist = ZoneInfo("Asia/Kolkata")
    start = datetime(2026, 11, 25, 9, 15, tzinfo=ist)
    bars = [IndexBar15m(start + timedelta(minutes=15 * i), start + timedelta(minutes=15 * (i + 1)),
                        start + timedelta(minutes=15 * (i + 1), seconds=2),
                        25000 + i, 25002 + i, 24998 + i, 25001 + i)
            for i in range(20)]
    forecast_at = bars[-1].end + timedelta(seconds=5)
    expiry = date(2026, 11, 30)
    observed = datetime(2026, 11, 25, 5, tzinfo=timezone.utc)
    long_quote = Quote(111, "NIFTY26NOV25000CE", expiry, 25, observed, 99.9, 100.0, 2500, 2500)
    short_quote = Quote(112, "NIFTY26NOV25100CE", expiry, 25, observed, 39.9, 40.0, 2500, 2500)
    policy = PremiumExitPolicy(80, 120, datetime(2026, 11, 29, 9, tzinfo=timezone.utc),
                               datetime(2026, 11, 30, 10, tzinfo=timezone.utc))

    def workload(index: int) -> tuple[float, float, float]:
        t0 = time.perf_counter()
        features = features_at(bars, forecast_at, start)
        forecast = random_walk_baseline(max(0.0001, features["vol_session"]))
        t1 = time.perf_counter()
        spread = debit_vertical(Leg(long_quote, "NIFTY", "CE", 25000),
                                Leg(short_quote, "NIFTY", "CE", 25100))
        t2 = time.perf_counter()
        entry = Quote(321, "NIFTY26NOV25000CE", expiry, 25, observed, 99.9, 100.0, 2500, 2500)
        quote_at = observed + timedelta(seconds=1)
        exit_quote = Quote(321, "NIFTY26NOV25000CE", expiry, 25, quote_at, 79.9, 80.0, 2500, 2500)
        exit_result = manage_long_option_exit(
            entry=entry, lots=1, policy=policy,
            quotes=(OptionExitQuote(exit_quote, quote_at + timedelta(milliseconds=5)),),
            max_quote_age_seconds=2)
        t3 = time.perf_counter()
        assert forecast["method"] == "RANDOM_WALK"
        assert spread["broker_margin_verified"] is False
        assert exit_result["trigger"] == "PREMIUM_STOP"
        assert exit_result["broker_order_created"] is False
        return (t1 - t0, t2 - t1, t3 - t2)

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=12) as pool:
        timings = list(pool.map(workload, range(240)))
    wall_seconds = time.perf_counter() - started
    assert calls == []
    assert len(timings) == 240
    assert wall_seconds < 30  # broad runaway guard, not a product latency SLA
    for column in range(3):
        values = sorted(row[column] * 1000 for row in timings)
        assert statistics.median(values) > 0

    metrics = {"schema": "fb.concurrent_paper_load_smoke.v1", "workload": {
        "concurrency": 12, "iterations": len(timings), "wall_seconds": wall_seconds,
        "operations_per_iteration": ["15m_point_in_time_features_and_baseline_forecast",
                                      "defined_risk_option_vertical", "observed_quote_protective_exit"],
        "broker_write_attempts": len(calls), "network_attempts": len(calls),
        "physical_orders_enabled_was_forged_true": True,
        "environment_scope": "local synthetic smoke benchmark; not a production SLA"},
        "latency_ms": {name: {"median": statistics.median(row[column] * 1000 for row in timings),
                               "p95": sorted(row[column] * 1000 for row in timings)[int(len(timings) * .95) - 1]}
                       for name, column in (("forecast", 0), ("options_chain_proxy", 1),
                                            ("protective_exit", 2))}}
    metrics["report_sha256"] = __import__("hashlib").sha256(
        json.dumps(metrics, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    assert len(metrics["report_sha256"]) == 64
    print("LOAD_SMOKE_METRICS=" + json.dumps(metrics, sort_keys=True, separators=(",", ":")))


def test_missing_or_invalid_performance_data_fails_closed():
    with pytest.raises(PerformanceReportError, match="at least two sessions"):
        build_performance_report(sessions=(_sessions()[0],), trades=(PerformanceTrade(1, 10),))
    with pytest.raises(PerformanceReportError, match="chronological"):
        build_performance_report(sessions=tuple(reversed(_sessions())),
                                 trades=(PerformanceTrade(1, 10),))
    with pytest.raises(PerformanceReportError, match="finite"):
        build_performance_report(
            sessions=(_sessions()[0], _sessions()[1],
                      PerformanceSession(date(2026, 11, 2), float("nan"), 0, 1, 0, 0, 0)),
            trades=(PerformanceTrade(1, 10),))
