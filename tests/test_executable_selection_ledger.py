from datetime import date, datetime, timedelta, timezone

import pytest

from financial_brain.evaluation.executable import ExecutableOutcome, outcome_sha256
from financial_brain.evaluation.executable_ledger import ExecutableLedgerError, ExecutableSelectionLedger


DAY = date(2026, 11, 2)
T0 = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)


def test_frozen_selection_scores_missing_and_no_fill_without_dropping_picks(tmp_path):
    now = [T0]
    ledger = ExecutableSelectionLedger(tmp_path / "outcomes.sqlite", clock=lambda: now[0])
    ledger.freeze("run-1", selected=[(DAY, "momentum", "ABC"), (DAY, "momentum", "XYZ")],
                  scheduled_days=[DAY], horizon_sessions=1, annual_cash_rate=0.0)
    now[0] += timedelta(minutes=1)
    no_fill = ExecutableOutcome(DAY, "momentum", "ABC", 0.0, None, 0.0, "0" * 64)
    no_fill = ExecutableOutcome(DAY, "momentum", "ABC", 0.0, None, 0.0, outcome_sha256(no_fill))
    result = ledger.score("run-1", outcomes=[no_fill], outcome_available_at=now[0])
    assert result["result"]["sessions"]["2026-11-02"]["selected_count"] == 2
    assert result["result"]["missing_outcome_count"] == 1
    ledger.close()

    reopened = ExecutableSelectionLedger(tmp_path / "outcomes.sqlite", clock=lambda: now[0])
    assert reopened.result("run-1")["score_sha256"] == result["score_sha256"]
    with pytest.raises(ExecutableLedgerError, match="no prior score"):
        reopened.score("run-1", outcomes=[], outcome_available_at=now[0])
    with pytest.raises(ExecutableLedgerError, match="already registered"):
        reopened.freeze("run-1", selected=[(DAY, "momentum", "ABC")], scheduled_days=[DAY],
                        horizon_sessions=1, annual_cash_rate=0.0)
    reopened.close()


def test_selection_must_precede_declared_outcome_availability(tmp_path):
    now = [T0]
    ledger = ExecutableSelectionLedger(tmp_path / "outcomes.sqlite", clock=lambda: now[0])
    ledger.freeze("run-1", selected=[(DAY, "momentum", "ABC")], scheduled_days=[DAY],
                  horizon_sessions=1, annual_cash_rate=0.0)
    now[0] += timedelta(minutes=1)
    with pytest.raises(ExecutableLedgerError, match="frozen after"):
        ledger.score("run-1", outcomes=[], outcome_available_at=T0)
    ledger.close()


def test_ledger_detects_event_tampering(tmp_path):
    path = tmp_path / "outcomes.sqlite"
    ledger = ExecutableSelectionLedger(path, clock=lambda: T0)
    ledger.freeze("run-1", selected=[(DAY, "momentum", "ABC")], scheduled_days=[DAY],
                  horizon_sessions=1, annual_cash_rate=0.0)
    ledger._db.execute("UPDATE executable_events SET body_json='{}' WHERE run_id='run-1'")
    with pytest.raises(ExecutableLedgerError, match="hash chain"):
        ledger.audit()
    ledger.close()
