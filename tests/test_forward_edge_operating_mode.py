from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.forward_edge.operating_mode import (
    OperatingMode,
    OperatingModeError,
    OperatingModeLedger,
)


NOW = datetime(2026, 11, 27, 4, 0, tzinfo=timezone.utc)


def test_operating_modes_are_durable_audited_and_require_explicit_recovery():
    connection = sqlite3.connect(":memory:")
    ledger = OperatingModeLedger(connection)
    assert ledger.initialize("s", occurred_at=NOW)
    assert ledger.mode("s") is OperatingMode.ACTIVE
    assert ledger.transition("s", OperatingMode.DEGRADED, event_id="stale-quote",
                             occurred_at=NOW + timedelta(seconds=1), reason="quote exceeded freshness")
    with pytest.raises(OperatingModeError, match="invalid operating mode transition"):
        ledger.transition("s", OperatingMode.ACTIVE, event_id="skip-recovery",
                          occurred_at=NOW + timedelta(seconds=2), reason="fresh quote arrived")
    ledger.transition("s", OperatingMode.RECOVERING, event_id="recovery-start",
                      occurred_at=NOW + timedelta(seconds=2), reason="operator acknowledged fresh feed")
    ledger.transition("s", OperatingMode.ACTIVE, event_id="recovery-complete",
                      occurred_at=NOW + timedelta(seconds=3), reason="recovery checks passed")
    assert ledger.mode("s") is OperatingMode.ACTIVE
    assert len(ledger.events("s")) == 4
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        connection.execute("DELETE FROM operating_mode_events")
    connection.close()
