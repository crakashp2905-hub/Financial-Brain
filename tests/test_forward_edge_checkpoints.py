from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from financial_brain.forward_edge.market_events import EventKind, MarketEvent
from financial_brain.forward_edge.session_runner import ForwardSessionRunner, ForwardSessionRunnerError
from financial_brain.forward_edge.session_state import ForwardRunConfig, ForwardSessionStore


NOW = datetime(2026, 11, 13, 4, 45, tzinfo=timezone.utc)


def _config() -> ForwardRunConfig:
    return ForwardRunConfig(date(2026, 11, 13), "cohort", "a" * 64, "NIFTY50_TRI",
                            ("NSE:INFY",), Decimal("100000"), {}, "ledger-1")


def _event() -> MarketEvent:
    return MarketEvent("quote-1", EventKind.QUOTE, "NSE", "NSE:INFY", NOW, NOW, NOW,
                       {"bid": 100.0, "ask": 100.1, "bid_quantity": 10, "ask_quantity": 10},
                       "fixture", evidence_id="b" * 64)


def _runner(tmp_path) -> ForwardSessionRunner:
    return ForwardSessionRunner(ForwardSessionStore(tmp_path / "state.sqlite"),
                                tmp_path / "journal.sqlite")


def test_start_and_end_checkpoints_are_immutable_and_detect_journal_changes(tmp_path):
    runner = _runner(tmp_path)
    runner.begin("s", _config(), now=NOW, preflight={"risk_policy_loaded": True})
    start = runner.checkpoint("s", phase="START", now=NOW)
    assert start["ledger_status"] == "RECONCILED_GROSS"
    assert len(start["ledger_sha256"]) == 64

    runner.ingest("s", _config(), [_event()], ingest_metadata={
        "quote-1": (NOW, "TEST_CLOCK")})
    retry_start = runner.checkpoint("s", phase="START", now=NOW.replace(second=1))
    assert retry_start == start
    runner.close_session("s", complete=False, now=NOW.replace(second=2), reason="fixture stop")
    end = runner.checkpoint("s", phase="END", now=NOW.replace(second=3))
    verified = runner.verify_checkpoint("s", end["checkpoint_id"])
    assert verified["immutable_evidence_valid"] is True
    assert verified["current_records_match"] is True
    assert verified["verification_complete"] is False  # The journal checkpoint is not atomically bound to the ledger.

    runner._con.execute("UPDATE runner_events SET ingested_at=? WHERE session_id=? AND event_id=?",
                        [(NOW.replace(second=4)).isoformat(), "s", "quote-1"])
    runner._con.commit()
    changed = runner.verify_checkpoint("s", end["checkpoint_id"])
    assert changed["immutable_evidence_valid"] is True
    assert changed["current_records_match"] is False
    runner.close()


def test_checkpoint_phase_and_terminal_requirements_fail_closed(tmp_path):
    runner = _runner(tmp_path)
    runner.begin("s", _config(), now=NOW, preflight={"risk_policy_loaded": True})
    with pytest.raises(ForwardSessionRunnerError, match="START or END"):
        runner.checkpoint("s", phase="MIDDAY", now=NOW)
    with pytest.raises(ForwardSessionRunnerError, match="terminal session state"):
        runner.checkpoint("s", phase="END", now=NOW)
    runner.close()
