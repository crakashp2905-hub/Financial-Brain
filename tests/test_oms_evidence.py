from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from financial_brain.execution.oms import evidence


class FakeJournal:
    def __init__(self, events, orders):
        self._events = events
        self._orders = orders

    def events(self):
        return self._events

    def orders(self, *, states):
        assert states == {"FILLED", "PARTIAL", "CANCELLED"}
        return self._orders

    def verify(self):
        return {"ok": True}


def test_gather_uses_stage_journal_events_orders_and_finite_shortfall_values(tmp_path):
    today = date.today()
    at = datetime.now(timezone.utc).isoformat()
    events = [
        {"at": at, "event": "SUBMITTED", "payload": {"day": today.isoformat()}},
        {"at": at, "event": "BROKER_UPDATE", "payload": {"day": today.isoformat()}},
        {"at": at, "event": "RECONCILE_MISMATCH", "payload": {}},
        {"at": at, "event": "KILL_SWITCH_TRIPPED", "payload": {}},
        {"at": at, "event": "REHEARSAL_SESSION", "payload": {"day": today.isoformat()}},
    ]
    orders = [SimpleNamespace(filled_qty=2, strategy="alpha", day=today.isoformat()),
              SimpleNamespace(filled_qty=0, strategy="alpha", day=today.isoformat()),
              SimpleNamespace(filled_qty=1, strategy="other", day=today.isoformat())]
    result = evidence.gather(FakeJournal(events, orders), strategy="alpha", verdict="CANDIDATE",
                             runbook_read=True, oos_verdict="CONFIRMED",
                             shortfall_ratios=[1.0, None, float("nan"), 3.0], stage_path=tmp_path / "missing.json")
    assert result["paper_oms_sessions"] == 1
    assert result["journal_chain_ok"] is True
    assert result["runbook_read_by_owner"] is True
    assert result["sessions_at_stage"] == 1
    assert result["filled_orders_at_stage"] == 1
    assert result["reconciliation_mismatches"] == 1
    assert result["kill_switch_trips_last_10"] == 1
    assert result["median_shortfall_over_model_max"] == pytest.approx(2.0)
    assert result["live_inside_backtest_band"] is True
    assert result["gathered_at"]


def test_gather_handles_empty_observations_without_claiming_live_validation(tmp_path):
    result = evidence.gather(FakeJournal([], []), strategy="alpha", verdict="PENDING",
                             stage_path=tmp_path / "missing.json")
    assert result["paper_oms_sessions"] == 0
    assert result["sessions_at_stage"] == 0
    assert result["filled_orders_at_stage"] == 0
    assert result["median_shortfall_over_model_max"] is None
    assert result["live_inside_backtest_band"] is None
