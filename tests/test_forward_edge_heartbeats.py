from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.forward_edge.heartbeats import (
    COMPONENTS, HeartbeatError, SessionHeartbeatLedger,
)

NOW = datetime(2026, 11, 27, 4, 0, tzinfo=timezone.utc)


def test_component_heartbeats_require_each_independent_producer_and_track_last_good_event():
    connection = sqlite3.connect(":memory:")
    ledger = SessionHeartbeatLedger(connection)
    assert ledger.record(session_id="s", component="quote_feed", heartbeat_id="q1",
                         occurred_at=NOW, last_good_event_id="quote-1",
                         last_good_event_at=NOW - timedelta(milliseconds=2))
    snapshot = ledger.snapshot("s", now=NOW, stale_after=timedelta(seconds=10))
    assert snapshot["status"] == "DEGRADED"
    assert snapshot["entry_readiness"] == "NO_ENTRY"
    assert snapshot["components"]["quote_feed"]["last_good_event_id"] == "quote-1"
    assert snapshot["missing_or_stale"] == [name for name in COMPONENTS if name != "quote_feed"]

    for component in COMPONENTS[1:]:
        ledger.record(session_id="s", component=component, heartbeat_id=f"{component}-1",
                      occurred_at=NOW)
    ready = ledger.snapshot("s", now=NOW, stale_after=timedelta(seconds=10))
    assert ready["status"] == "HEALTHY"
    assert ready["entry_readiness"] == "READY"
    assert ready["missing_or_stale"] == []
    connection.close()


def test_heartbeats_fail_on_regression_conflict_or_staleness():
    connection = sqlite3.connect(":memory:")
    ledger = SessionHeartbeatLedger(connection)
    ledger.record(session_id="s", component="strategy_process", heartbeat_id="d1",
                  occurred_at=NOW, last_good_event_id="decision-1", last_good_event_at=NOW)
    assert not ledger.record(session_id="s", component="strategy_process", heartbeat_id="d1",
                             occurred_at=NOW, last_good_event_id="decision-1", last_good_event_at=NOW)
    with pytest.raises(HeartbeatError, match="conflicting content"):
        ledger.record(session_id="s", component="strategy_process", heartbeat_id="d1",
                      occurred_at=NOW, last_good_event_id="decision-2", last_good_event_at=NOW)
    with pytest.raises(HeartbeatError, match="cannot move backwards"):
        ledger.record(session_id="s", component="strategy_process", heartbeat_id="d0",
                      occurred_at=NOW - timedelta(seconds=1))
    assert ledger.record(session_id="s", component="strategy_process", heartbeat_id="late-event",
                         occurred_at=NOW + timedelta(seconds=1), last_good_event_id="older-event",
                         last_good_event_at=NOW - timedelta(seconds=1))
    assert ledger.latest("s")["components"]["strategy_process"]["last_good_event_id"] == "decision-1"
    stale = ledger.snapshot("s", now=NOW + timedelta(seconds=12), stale_after=timedelta(seconds=10))
    assert stale["components"]["strategy_process"]["status"] == "STALE"
    assert stale["entry_readiness"] == "NO_ENTRY"
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        connection.execute("DELETE FROM session_heartbeat_events")
    connection.close()
