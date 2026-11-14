from datetime import date, datetime, timedelta, timezone

import pytest

from financial_brain.forward_edge.book_adapter import BookAdapterError, forward_position_events
from financial_brain.forward_edge.holding_period_schedule import (
    ExchangeSessionCalendar, HoldingScheduleError, schedule_holding_exit,
    scheduled_exit_order_intents,
)
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


def _calendar_clock(day):
    observed = datetime.combine(day, datetime.min.time(), tzinfo=UTC) + timedelta(hours=11)
    return SessionClockProvider(lambda: observed)


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


def test_calendar_schedules_exit_on_later_exchange_session_across_weekend(tmp_path):
    con = B.connect(tmp_path / "book.duckdb")
    _insert_position(con, signal_day=date(2026, 11, 10), entry_day=date(2026, 11, 11),
                     last_day=date(2026, 11, 11), held=0, horizon=3)
    calendar = ExchangeSessionCalendar(
        "NSE-CALENDAR-2026-v1",
        (date(2026, 11, 11), date(2026, 11, 12), date(2026, 11, 13),
         date(2026, 11, 16), date(2026, 11, 17)),
        "a" * 64,
    )
    scheduled, = forward_position_events(
        con, mode="live", spec=B.MOMENTUM, as_of=date(2026, 11, 13),
        clock_provider=_calendar_clock(date(2026, 11, 13)), session_calendar=calendar,
    )
    fields = dict(scheduled.payload)
    assert fields["position_state"] == "ACTIVE"
    assert fields["scheduled_exit_session"] == "2026-11-16"
    assert fields["exit_schedule_status"] == "SCHEDULED"
    assert fields["remaining_sessions"] == "1"
    assert fields["holding_clock"] == "EXCHANGE_SESSIONS_FROM_ENTRY"
    with pytest.raises(BookAdapterError, match="clock precedes"):
        forward_position_events(
            con, mode="live", spec=B.MOMENTUM, as_of=date(2026, 11, 13),
            clock_provider=_clock(), session_calendar=calendar,
        )

    due, = forward_position_events(
        con, mode="live", spec=B.MOMENTUM, as_of=date(2026, 11, 16),
        clock_provider=_calendar_clock(date(2026, 11, 16)), session_calendar=calendar,
    )
    assert dict(due.payload)["position_state"] == "EXIT_DUE"
    order, = scheduled_exit_order_intents([due], exchange_session=date(2026, 11, 16))
    assert order["side"] == "SELL"
    assert order["position_effect"] == "CLOSE"
    assert order["quantity"] == 10
    assert order["scheduled_exit_session"] == "2026-11-16"
    overdue, = forward_position_events(
        con, mode="live", spec=B.MOMENTUM, as_of=date(2026, 11, 17),
        clock_provider=_calendar_clock(date(2026, 11, 17)), session_calendar=calendar,
    )
    assert dict(overdue.payload)["position_state"] == "EXIT_OVERDUE"
    late_order, = scheduled_exit_order_intents([overdue], exchange_session=date(2026, 11, 17))
    assert late_order["order_id"] == order["order_id"]
    assert late_order["reason"] == "OVERDUE_TIME_EXIT"


def test_holding_schedule_requires_calendar_coverage_and_exchange_dates():
    calendar = ExchangeSessionCalendar("NSE", (date(2026, 11, 12), date(2026, 11, 13)), "b" * 64)
    with pytest.raises(HoldingScheduleError, match="does not cover"):
        schedule_holding_exit(entry_session=date(2026, 11, 12), horizon_sessions=2,
                              as_of_session=date(2026, 11, 12), calendar=calendar)
    with pytest.raises(HoldingScheduleError, match="must exist"):
        schedule_holding_exit(entry_session=date(2026, 11, 12), horizon_sessions=1,
                              as_of_session=date(2026, 11, 14), calendar=calendar)
