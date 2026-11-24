from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import pytest

from financial_brain.strategies.four_node.replay import (
    ReplayBar,
    ReplayEligibility,
    compare_stream_and_replay,
)
from financial_brain.forward_edge.venue_calendar import Session, VenueCalendar

START = datetime(2026, 11, 24, 9, 15, tzinfo=timezone.utc)
HASH = "a" * 64


def _eligibility(*, available_at: datetime = START, tradable: bool = True) -> ReplayEligibility:
    return ReplayEligibility("NIFTY", START, available_at, HASH, tradable)


def _bars() -> list[ReplayBar]:
    rows = [
        (99, 100, 98, 99),
        (100, 110, 99, 105),
        (105, 106, 95, 100),
        (100, 105, 90, 95),
        (95, 100, 95, 98),
        (98, 108, 97, 104),
        (104, 106, 100, 105),
        (99, 101, 88, 89),
    ]
    return [ReplayBar(f"source-bar-{i}", "NIFTY", "NSE:2026-11-24", i,
                      START + timedelta(minutes=i), *values, HASH, _eligibility())
            for i, values in enumerate(rows)]


def test_prefix_only_pivots_have_confirmation_latency_and_stream_replay_parity():
    result = compare_stream_and_replay(_bars(), left=1, right=1, warmup_bars=1)
    assert result.matches
    assert result.warmed_bars == 8
    assert all(event.observed_at is not None for event in result.events)
    candidate, = result.candidates
    assert candidate.side == "SELL"
    assert candidate.breakout_bar == 7 and candidate.actionable_after_bar == 8
    assert candidate.breakout_at == START + timedelta(minutes=7)
    assert candidate.available_at == candidate.breakout_at
    assert candidate.source_sha256 is not None and len(candidate.source_sha256) == 64
    node_events = [event for event in result.events if event.kind == "NODE_CONFIRMED"]
    assert [event.node.confirmed_at for event in node_events if event.node is not None][:3] == [2, 4, 6]
    assert all(event.observed_at == START + timedelta(minutes=event.bar_index) for event in node_events)


def test_replay_preserves_source_and_later_availability_clocks():
    delayed = [ReplayBar(
        bar.event_id, bar.instrument_id, bar.session_id, bar.bar_index, bar.observed_at,
        bar.open, bar.high, bar.low, bar.close, bar.source_sha256, bar.eligibility,
        available_at=bar.observed_at + timedelta(seconds=3),
    ) for bar in _bars()]
    result = compare_stream_and_replay(delayed, left=1, right=1, warmup_bars=1)
    candidate, = result.candidates
    assert candidate.breakout_at == START + timedelta(minutes=7)
    assert candidate.available_at == START + timedelta(minutes=7, seconds=3)
    signal_event = next(event for event in result.events
                        if event.kind == "BAR_CLOSED" and event.bar_index == 7)
    assert signal_event.observed_at == START + timedelta(minutes=7)
    assert signal_event.available_at == START + timedelta(minutes=7, seconds=3)
    assert signal_event.source_sha256 == HASH
    candidate, = result.candidates
    assert candidate.source_sha256 is not None and len(candidate.source_sha256) == 64


def test_historical_rights_and_warmup_fail_closed():
    unavailable = [ReplayBar(
        bar.event_id, bar.instrument_id, bar.session_id, bar.bar_index, bar.observed_at,
        bar.open, bar.high, bar.low, bar.close, bar.source_sha256,
        _eligibility(available_at=START + timedelta(hours=1)),
    ) for bar in _bars()]
    assert compare_stream_and_replay(unavailable, left=1, right=1, warmup_bars=1).candidates == ()
    assert compare_stream_and_replay(_bars(), left=1, right=1, warmup_bars=100).candidates == ()


def test_missing_bars_duplicate_ids_and_invalid_timestamp_contract_are_rejected():
    bars = _bars()
    gap = bars.copy()
    gap[4] = ReplayBar("source-bar-4", "NIFTY", "NSE:2026-11-24", 4,
                       START + timedelta(minutes=6), 95, 100, 95, 98, HASH, _eligibility())
    with pytest.raises(ValueError, match="missing or irregular"):
        compare_stream_and_replay(gap, left=1, right=1, warmup_bars=1)
    duplicate = bars.copy()
    duplicate[1] = ReplayBar("source-bar-0", "NIFTY", "NSE:2026-11-24", 1,
                             START + timedelta(minutes=1), 100, 110, 99, 105, HASH, _eligibility())
    with pytest.raises(ValueError, match="duplicate replay bar"):
        compare_stream_and_replay(duplicate, left=1, right=1, warmup_bars=1)
    with pytest.raises(ValueError, match="timezone-aware"):
        ReplayEligibility("NIFTY", datetime(2026, 11, 24), START, HASH, True)


def test_session_rollover_resets_nodes_across_market_closure_and_revisited_session_fails():
    first_session = _bars()
    next_day = START + timedelta(days=1)
    next_session = ReplayBar(
        "next-session-bar-0", "NIFTY", "NSE:2026-11-25", 0, next_day,
        100, 101, 99, 100, HASH, _eligibility(),
    )
    replay = compare_stream_and_replay(
        [*first_session, next_session], left=1, right=1, warmup_bars=1,
    )
    rollover = replay.historical_results[-1]
    assert "SESSION_ROLLOVER_RESET" in rollover.reason_codes
    assert rollover.node_ids == ()

    repeated_session = ReplayBar(
        "revisited-session-bar", "NIFTY", "NSE:2026-11-24", 0,
        next_day + timedelta(minutes=1), 100, 101, 99, 100, HASH, _eligibility(),
    )
    with pytest.raises(ValueError, match="sessions must be unique"):
        compare_stream_and_replay(
            [*first_session, next_session, repeated_session],
            left=1, right=1, warmup_bars=1,
        )


def test_official_venue_calendar_fails_closed_for_holidays_and_uncovered_dates():
    day = date(2026, 11, 24)
    official_session = Session(
        "NSE", day, time(9, 15), time(15, 30), "REGULAR", "NSE calendar", HASH,
        datetime(2026, 11, 20, tzinfo=timezone.utc),
    )
    calendar = VenueCalendar([official_session], source_as_of=day)
    assert compare_stream_and_replay(
        _bars(), left=1, right=1, warmup_bars=1, venue_calendar=calendar, venue="NSE",
    ).matches

    holiday_bar = ReplayBar(
        "holiday-bar", "NIFTY", "NSE:2026-11-25", 0,
        datetime(2026, 11, 25, 9, 15, tzinfo=timezone.utc),
        100, 101, 99, 100, HASH, _eligibility(),
    )
    covered_through_holiday = VenueCalendar([], source_as_of=date(2026, 11, 25))
    with pytest.raises(ValueError, match="non-trading venue date"):
        compare_stream_and_replay(
            [holiday_bar], venue_calendar=covered_through_holiday, venue="NSE",
        )

    with pytest.raises(ValueError, match="sourced venue-calendar coverage"):
        compare_stream_and_replay(
            [holiday_bar], venue_calendar=calendar, venue="NSE",
        )

    with pytest.raises(ValueError, match="session ID does not match"):
        compare_stream_and_replay(
            [ReplayBar("wrong-session", "NIFTY", "NSE:2026-11-25", 0, START,
                       100, 101, 99, 100, HASH, _eligibility())],
            venue_calendar=calendar, venue="NSE",
        )
