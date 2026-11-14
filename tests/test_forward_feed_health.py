from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.forward_edge.durable import DurableJournal
from financial_brain.forward_edge.market_data import DataError, FeedHealth, entry_allowed
from financial_brain.forward_edge.modes import Mode
from financial_brain.forward_edge.session_ops import PacketDeduper
from financial_brain.forward_edge.session_runner import ForwardRunConfig, ForwardSessionRunner


NOW = datetime(2026, 11, 12, 4, 0, tzinfo=timezone.utc)


def _config():
    return ForwardRunConfig(
        session_id="feed-health-session", exchange_date="2026-11-12", cohort_id="feed-health",
        strategy_sha256="a" * 64, risk_policy_sha256="b" * 64, constraints_sha256="c" * 64,
        ledger_id="feed-ledger", benchmark_id="CASH", instruments=("NSE:INFY",),
        capital_inr="100000", mode=Mode.LIVE_PAPER,
    )


def test_packet_monitor_counts_gaps_duplicates_disconnects_reconnects_and_drops():
    monitor = PacketDeduper()
    assert monitor.offer("p1", 10, {}, NOW)
    assert not monitor.offer("p1", 10, {}, NOW + timedelta(milliseconds=1))
    assert monitor.offer("p4", 13, {}, NOW + timedelta(milliseconds=2))
    assert monitor.offer("p3-late", 12, {}, NOW + timedelta(milliseconds=3))
    monitor.heartbeat(NOW + timedelta(seconds=1))
    monitor.disconnect()
    monitor.reconnect()
    monitor.record_drop(2)

    health = monitor.feed_health()
    assert (health.gaps, health.duplicates, health.disconnects, health.reconnects,
            health.dropped_updates) == (2, 1, 1, 1, 2)
    assert health.last_available_utc == NOW + timedelta(milliseconds=3)
    assert health.last_heartbeat_utc == NOW + timedelta(seconds=1)
    assert monitor.ordered() == [{}, {}, {}]


def test_feed_health_rejects_future_quote_and_stale_heartbeat():
    health = FeedHealth(NOW + timedelta(seconds=1), 0, 0, 0,
                        last_heartbeat_utc=NOW - timedelta(seconds=10))
    allowed, reasons = entry_allowed(health, NOW)
    assert not allowed
    assert reasons == ["FUTURE_QUOTE", "STALE_HEARTBEAT"]
    with pytest.raises(DataError, match="non-negative"):
        FeedHealth(NOW, -1, 0, 0)


def test_stale_required_feed_marks_session_incomplete_and_preserves_health_evidence(tmp_path):
    path = tmp_path / "feed-health.jsonl"
    runner = ForwardSessionRunner(path)
    config = _config()
    runner.record_preflight(config, {"approved": True, "status": "PASS", "checks": {"local": True}})
    stale = FeedHealth(NOW - timedelta(seconds=20), 2, 1, 3, 4, 1,
                       last_heartbeat_utc=NOW - timedelta(seconds=20))
    runner.record_feed_health(config, stale, observed_at=NOW)
    # Identical samples are idempotent.
    runner.record_feed_health(config, stale, observed_at=NOW)
    with pytest.raises(ValueError, match="monotonic"):
        runner.record_feed_health(config, stale, observed_at=NOW - timedelta(seconds=1))

    decision_calls = []
    execution_calls = []
    result = runner.run(
        config, [], decision_at=NOW,
        decision_fn=lambda _: decision_calls.append(True) or {"side": "BUY", "expected_net_alpha": 0.02, "forecast_horizon_sessions": 1, "forecast_costs_included": True, "net_alpha_forecast": {"horizon": 1, "expected_return": 0.033, "benchmark": 0.01, "fees": 0.001, "spread": 0.001, "impact": 0.001}},
        risk_fn=lambda _: {"approved": True},
        execution_fn=lambda *args: execution_calls.append(args) or {"status": "FILLED"},
    )
    assert result["status"] == "INCOMPLETE"
    assert result["reason"] == "FEED_ENDED_EARLY"
    assert decision_calls == [] and execution_calls == []
    health = result["feed_health"]["feed_health"]
    assert health["gaps"] == 2
    assert health["disconnects"] == 1
    assert health["duplicates"] == 4
    assert health["reconnects"] == 1
    assert health["dropped_updates"] == 3
    records = DurableJournal(path).records()
    assert sum(row["t"] == "FEED_HEALTH" for row in records) == 1
    incomplete = next(row for row in records if row["t"] == "SESSION_INCOMPLETE")
    assert incomplete["evidence"]["blocking_reasons"] == [
        "STALE_QUOTE", "STALE_HEARTBEAT", "QUOTE_GAP", "DISCONNECTED", "DROPPED_UPDATES"
    ]
    assert not any(row["t"] in {"PAPER_INTENT", "PAPER_EXECUTION"} for row in records)
