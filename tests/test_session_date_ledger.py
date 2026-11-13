from datetime import date, datetime, timedelta, timezone

import pytest

from financial_brain.forward_edge.market_data import Calendar
from financial_brain.forward_edge.modes import Mode
from financial_brain.forward_edge.session_ops import SessionDateLedger, SessionError
from financial_brain.forward_edge.session_runner import ForwardRunConfig, ForwardSessionRunner, RunnerError


UTC = timezone.utc
DEADLINE = datetime(2026, 11, 12, 11, 0, tzinfo=UTC)


def _config(session_id="session-date-1"):
    return ForwardRunConfig(
        session_id=session_id, exchange_date="2026-11-12", cohort_id="C1",
        strategy_sha256="a" * 64, risk_policy_sha256="b" * 64,
        constraints_sha256="c" * 64, ledger_id="LEDGER-1", benchmark_id="NIFTY50",
        instruments=("NSE:INFY",), capital_inr="1000000", mode=Mode.LIVE_PAPER,
    )


def test_calendar_holiday_is_persisted_as_no_trade_and_cannot_be_rewritten(tmp_path):
    ledger = SessionDateLedger(tmp_path / "dates.jsonl")
    calendar = Calendar("nse-fixture-1", frozenset({date(2026, 11, 12)}))
    first = ledger.plan_date("holiday-1", "2026-11-12", calendar, DEADLINE)
    assert first["status"] == "NO_TRADE"
    assert first["reason"] == "NON_TRADING_DATE"
    assert ledger.plan_date("holiday-1", "2026-11-12", calendar, DEADLINE) == first
    with pytest.raises(SessionError, match="immutable NO_TRADE"):
        ledger.record_result("holiday-1", "CLOSED", "anything")


def test_special_session_is_planned_and_closes_normally(tmp_path):
    ledger = SessionDateLedger(tmp_path / "dates.jsonl")
    calendar = Calendar("special-1", frozenset({date(2026, 11, 12)}),
                        frozenset({date(2026, 11, 12)}))
    assert ledger.plan_date("special-1", "2026-11-12", calendar, DEADLINE)["status"] == "PLANNED"
    result = ledger.record_result("special-1", "CLOSED", "SESSION_COMPLETE")
    assert result["status"] == "CLOSED"
    assert ledger.record_result("special-1", "CLOSED", "SESSION_COMPLETE") == result
    with pytest.raises(SessionError, match="immutable"):
        ledger.record_result("special-1", "INCOMPLETE", "FEED_LOSS")


def test_expired_unresolved_trading_dates_become_incomplete_once(tmp_path):
    ledger = SessionDateLedger(tmp_path / "dates.jsonl")
    calendar = Calendar("weekdays-1", frozenset())
    ledger.plan_date("missed-1", "2026-11-12", calendar, DEADLINE)
    assert ledger.expire_missing(DEADLINE - timedelta(seconds=1)) == []
    expired = ledger.expire_missing(DEADLINE)
    assert len(expired) == 1
    assert expired[0]["status"] == "INCOMPLETE"
    assert expired[0]["reason"] == "SESSION_RESULT_MISSING"
    assert ledger.expire_missing(DEADLINE + timedelta(days=1)) == []


def test_invalid_or_unplanned_outcomes_fail_closed(tmp_path):
    ledger = SessionDateLedger(tmp_path / "dates.jsonl")
    with pytest.raises(SessionError, match="planned"):
        ledger.record_result("missing", "CLOSED", "OK")
    with pytest.raises(SessionError, match="timezone-aware"):
        ledger.plan_date("bad-clock", "2026-11-12", Calendar("c1", frozenset()),
                         datetime(2026, 11, 12))


def test_runner_requires_calendar_plan_and_marks_feed_loss_incomplete(tmp_path, monkeypatch):
    ledger = SessionDateLedger(tmp_path / "dates.jsonl")
    calendar = Calendar("weekdays-1", frozenset())
    config = _config()
    runner = ForwardSessionRunner(tmp_path / "runner.jsonl", session_date_ledger=ledger)
    with pytest.raises(RunnerError, match="date must be recorded"):
        runner.begin(config)
    ledger.plan_date(config.session_id, config.exchange_date, calendar, DEADLINE)
    runner.begin(config)
    original_record = ledger.record_result

    def fail_once(*args, **kwargs):
        monkeypatch.setattr(ledger, "record_result", original_record)
        raise SessionError("simulated date-ledger outage")

    monkeypatch.setattr(ledger, "record_result", fail_once)
    with pytest.raises(RunnerError, match="simulated date-ledger outage"):
        runner.mark_incomplete(config.session_id, "FEED_LOSS")
    assert ledger.outcome(config.session_id) is None
    runner.mark_incomplete(config.session_id, "FEED_LOSS")
    assert ledger.outcome(config.session_id)["status"] == "INCOMPLETE"


def test_runner_persists_a_terminal_no_trade_date(tmp_path):
    ledger = SessionDateLedger(tmp_path / "dates.jsonl")
    calendar = Calendar("weekdays-1", frozenset())
    config = _config()
    ledger.plan_date(config.session_id, config.exchange_date, calendar, DEADLINE)
    runner = ForwardSessionRunner(tmp_path / "runner.jsonl", session_date_ledger=ledger)
    runner.record_preflight(config, {"approved": True, "status": "PASS"})
    result = runner.run(
        config, [], decision_at=DEADLINE - timedelta(minutes=1),
        decision_fn=lambda _: {"side": "NONE", "reason": "NO_SIGNAL"},
        risk_fn=lambda _: {"approved": True}, execution_fn=lambda *_: {},
    )
    assert result["status"] == "NO_TRADE"
    assert ledger.outcome(config.session_id)["status"] == "NO_TRADE"
