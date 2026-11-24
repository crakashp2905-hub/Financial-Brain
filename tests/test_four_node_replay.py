from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.strategies.four_node.replay import (
    ReplayBar,
    ReplayEligibility,
    compare_stream_and_replay,
)

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
