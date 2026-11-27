from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.forward_edge.session_recovery import SessionRecoveryLedger, retry_delay


NOW = datetime(2026, 11, 27, 4, 0, tzinfo=timezone.utc)


def test_exponential_retry_is_capped_and_requires_valid_policy():
    base, cap = timedelta(milliseconds=250), timedelta(milliseconds=900)
    assert [retry_delay(n, base=base, cap=cap).total_seconds() * 1000 for n in range(1, 6)] == [
        250.0, 500.0, 900.0, 900.0, 900.0]


def test_handler_recovery_persists_bounded_attempts_and_deduplicates_root_alerts():
    connection = sqlite3.connect(":memory:")
    ledger = SessionRecoveryLedger(connection)
    exc = TimeoutError("quote callback timed out")
    first = ledger.record_failure(session_id="s", consumer_id="c", event_id="e", handler="protective_exits",
                                  occurred_at=NOW, error=exc, max_attempts=3,
                                  base_delay=timedelta(milliseconds=100),
                                  max_delay=timedelta(milliseconds=500))
    second = ledger.record_failure(session_id="s", consumer_id="c", event_id="e", handler="protective_exits",
                                   occurred_at=NOW + timedelta(milliseconds=100), error=exc, max_attempts=3,
                                   base_delay=timedelta(milliseconds=100),
                                   max_delay=timedelta(milliseconds=500))
    third = ledger.record_failure(session_id="s", consumer_id="c", event_id="e", handler="protective_exits",
                                  occurred_at=NOW + timedelta(milliseconds=300), error=exc, max_attempts=3,
                                  base_delay=timedelta(milliseconds=100),
                                  max_delay=timedelta(milliseconds=500))

    assert first["retry_at"] == (NOW + timedelta(milliseconds=100)).isoformat()
    assert first["alert_created"] is True
    assert second["alert_created"] is False
    assert third["status"] == "HALTED" and third["retry_at"] is None
    assert ledger.state("s", "c", "e", "protective_exits")["status"] == "HALTED"
    assert connection.execute("SELECT COUNT(*) FROM handler_recovery_alerts").fetchone()[0] == 1
    with pytest.raises(ValueError, match="halted handler cannot be marked recovered"):
        ledger.record_success(session_id="s", consumer_id="c", event_id="e", handler="protective_exits",
                              occurred_at=NOW + timedelta(seconds=1))
    connection.close()
