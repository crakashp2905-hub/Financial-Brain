from datetime import date, datetime, timedelta, timezone

import pytest

from financial_brain.forward_edge.book_adapter import BookAdapterError, forward_position_events
from financial_brain.forward_edge.modes import Mode
from financial_brain.forward_edge.session_clock import SessionClockProvider
from financial_brain.forward_edge.session_runner import ForwardRunConfig, ForwardSessionRunner
from financial_brain.paper import book as B


UTC = timezone.utc
AS_OF = date(2026, 11, 12)
NOW = datetime(2026, 11, 12, 11, 0, tzinfo=UTC)


def _insert_position(con, *, signal_day, entry_day, last_day, held, horizon=20, ticker="INFY"):
    con.execute(
        """INSERT INTO positions (
            version, mode, signal_day, rank, ticker, lineage, type, horizon, shares,
            stop, ref_price, expected_net, cost, entry_day, entry_px, exit_day, exit_px,
            exit_reason, last_day, last_px, held, bench, why
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [B.MOMENTUM.version, "live", signal_day, 1, ticker, "LINEAGE-1", "momentum", horizon, 10,
         90.0, 100.0, 0.01, 1.0, entry_day, 100.0 if entry_day else None,
         None, None, None, last_day, 101.0 if last_day else None, held, 0.0, "12-1 momentum"],
    )


def _clock():
    ticks = iter(NOW + timedelta(milliseconds=step) for step in (1, 2, 3, 4, 5, 6, 7, 8, 9))
    return SessionClockProvider(lambda: next(ticks))


def _config():
    return ForwardRunConfig(
        session_id="book-horizon-session", exchange_date="2026-11-12", cohort_id="C1",
        strategy_sha256="a" * 64, risk_policy_sha256="b" * 64,
        constraints_sha256="c" * 64, ledger_id="LEDGER-1", benchmark_id="NIFTY50",
        instruments=("NSE:INFY",), capital_inr="1000000", mode=Mode.LIVE_PAPER,
    )


def test_twenty_session_position_survives_overnight_and_is_runner_visible(tmp_path):
    con = B.connect(tmp_path / "book.duckdb")
    _insert_position(con, signal_day=date(2026, 11, 10), entry_day=date(2026, 11, 11),
                     last_day=date(2026, 11, 11), held=0)
    clock = _clock()
    events = forward_position_events(con, mode="live", spec=B.MOMENTUM, as_of=AS_OF,
                                     clock_provider=clock)
    assert len(events) == 1
    event = events[0]
    payload = dict(event.payload)
    assert event.kind == "BOOK_POSITION"
    assert event.instrument == "NSE:INFY"
    assert payload["position_state"] == "ACTIVE"
    assert payload["horizon_sessions"] == "20"
    assert payload["remaining_sessions"] == "20"
    runner = ForwardSessionRunner(tmp_path / "runner.jsonl", clock)
    runner.record_preflight(_config(), {"approved": True, "status": "PASS"})
    result = runner.run(
        _config(), events,
        decision_fn=lambda visible: {"side": "NONE", "visible_position_count": len(visible)},
        risk_fn=lambda _: {"approved": True}, execution_fn=lambda *_: {},
    )
    assert result["decision"]["visible_position_count"] == 1


def test_signal_day_does_not_force_entry_or_expiry_from_intraday_time(tmp_path):
    con = B.connect(tmp_path / "book.duckdb")
    _insert_position(con, signal_day=AS_OF, entry_day=None, last_day=None, held=0)
    event, = forward_position_events(con, mode="live", spec=B.MOMENTUM, as_of=AS_OF,
                                     clock_provider=_clock())
    payload = dict(event.payload)
    assert payload["position_state"] == "ENTRY_PENDING"
    assert payload["entry_day"] == ""
    assert payload["held_sessions"] == "0"


def test_twenty_completed_holding_sessions_are_flagged_due_at_daily_close(tmp_path):
    con = B.connect(tmp_path / "book.duckdb")
    _insert_position(con, signal_day=date(2026, 10, 1), entry_day=date(2026, 10, 2),
                     last_day=AS_OF, held=20)
    event, = forward_position_events(con, mode="live", spec=B.MOMENTUM, as_of=AS_OF,
                                     clock_provider=_clock())
    assert dict(event.payload)["position_state"] == "EXIT_DUE"
    assert dict(event.payload)["remaining_sessions"] == "0"


def test_future_marks_and_horizon_corruption_are_rejected(tmp_path):
    con = B.connect(tmp_path / "book.duckdb")
    _insert_position(con, signal_day=date(2026, 11, 10), entry_day=date(2026, 11, 11),
                     last_day=date(2026, 11, 13), held=1)
    with pytest.raises(BookAdapterError, match="after the requested session"):
        forward_position_events(con, mode="live", spec=B.MOMENTUM, as_of=AS_OF,
                                clock_provider=_clock())

    con.execute("UPDATE positions SET held = 21")
    with pytest.raises(BookAdapterError, match="exceed"):
        forward_position_events(con, mode="live", spec=B.MOMENTUM, as_of=AS_OF,
                                clock_provider=_clock())
