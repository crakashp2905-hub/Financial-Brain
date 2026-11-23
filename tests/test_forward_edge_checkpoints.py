from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from financial_brain.forward_edge.market_events import EventKind, MarketEvent
from financial_brain.forward_edge.session_runner import ForwardSessionRunner, ForwardSessionRunnerError
from financial_brain.forward_edge.session_state import ForwardRunConfig, ForwardSessionStore


NOW = datetime(2026, 11, 13, 4, 45, tzinfo=timezone.utc)
READY_PREFLIGHT = {name: True for name in (
    "calendar", "credentials", "disk", "feeds", "data_contracts", "risk", "system_health",
    "broker_disabled",
)}


def _config() -> ForwardRunConfig:
    return ForwardRunConfig(date(2026, 11, 13), "cohort", "a" * 64, "NIFTY50_TRI",
                            ("NSE:INFY",), Decimal("100000"),
                            {"allow_synthetic_fixture_events": True}, "ledger-1")


def _event() -> MarketEvent:
    return MarketEvent("quote-1", EventKind.QUOTE, "NSE", "NSE:INFY", NOW, NOW, NOW,
                       {"bid": 100.0, "ask": 100.1, "bid_quantity": 10, "ask_quantity": 10},
                       "SYNTHETIC_FIXTURE", evidence_id="b" * 64)


def _runner(tmp_path) -> ForwardSessionRunner:
    return ForwardSessionRunner(ForwardSessionStore(tmp_path / "state.sqlite"),
                                tmp_path / "journal.sqlite")


def test_start_and_end_checkpoints_are_immutable_and_detect_journal_changes(tmp_path):
    runner = _runner(tmp_path)
    runner.begin("s", _config(), now=NOW, preflight={**READY_PREFLIGHT, "risk_policy_loaded": True})
    start = runner.checkpoint("s", phase="START", now=NOW)
    assert start["ledger_status"] == "RECONCILED_GROSS"
    assert len(start["ledger_sha256"]) == 64
    assert start["atomic_with_ledger"] is True

    runner.ingest("s", _config(), [_event()], ingest_metadata={
        "quote-1": (NOW, "TEST_CLOCK")})
    retry_start = runner.checkpoint("s", phase="START", now=NOW.replace(second=1))
    assert retry_start == start
    runner.close_session("s", complete=False, now=NOW.replace(second=2), reason="fixture stop")
    end = runner.checkpoint("s", phase="END", now=NOW.replace(second=3))
    verified = runner.verify_checkpoint("s", end["checkpoint_id"])
    assert verified["immutable_evidence_valid"] is True
    assert verified["current_records_match"] is True
    assert verified["verification_complete"] is True

    runner._con.execute("UPDATE runner_events SET ingested_at=? WHERE session_id=? AND event_id=?",
                        [(NOW.replace(second=4)).isoformat(), "s", "quote-1"])
    runner._con.commit()
    changed = runner.verify_checkpoint("s", end["checkpoint_id"])
    assert changed["immutable_evidence_valid"] is True
    assert changed["current_records_match"] is False
    runner.close()


def test_complete_session_checkpoint_binds_execution_outbox_and_accounting_in_one_database(tmp_path):
    runner = _runner(tmp_path)
    runner.begin("s", _config(), now=NOW, preflight={**READY_PREFLIGHT, "risk_policy_loaded": True})
    runner.close_session("s", complete=True, now=NOW + timedelta(seconds=1))
    end = runner.checkpoint("s", phase="END", now=NOW + timedelta(seconds=2))
    assert end["atomic_with_ledger"] is True
    assert runner.verify_checkpoint("s", end["checkpoint_id"])["verification_complete"] is True
    assert not (tmp_path / "journal-outbox.sqlite").exists()
    assert runner._con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='outbox'").fetchone()
    runner.close()


def test_checkpoint_phase_and_terminal_requirements_fail_closed(tmp_path):
    runner = _runner(tmp_path)
    runner.begin("s", _config(), now=NOW, preflight={**READY_PREFLIGHT, "risk_policy_loaded": True})
    with pytest.raises(ForwardSessionRunnerError, match="START or END"):
        runner.checkpoint("s", phase="MIDDAY", now=NOW)
    with pytest.raises(ForwardSessionRunnerError, match="terminal session state"):
        runner.checkpoint("s", phase="END", now=NOW)
    runner.close()
