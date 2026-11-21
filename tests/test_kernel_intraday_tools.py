from __future__ import annotations

import pytest

from financial_brain.kernel.intraday_tools import (
    ReplayControl,
    SessionControl,
    anchored_vwap,
    completed_htf_trend,
    opening_range,
)


def test_opening_range_needs_enough_bars() -> None:
    assert opening_range([10.0, 12.0, 11.0], [9.0, 10.0, 8.5], 2) == (12.0, 9.0)
    assert opening_range([10.0], [9.0], 3) is None


def test_anchored_vwap_starts_at_the_anchor() -> None:
    highs = [10.0, 12.0, 14.0]
    lows = [8.0, 10.0, 12.0]
    closes = [9.0, 11.0, 13.0]
    vols = [100.0, 100.0, 200.0]
    full = anchored_vwap(highs, lows, closes, vols, anchor=0)
    from_last = anchored_vwap(highs, lows, closes, vols, anchor=2)
    assert from_last == pytest.approx(13.0)  # only bar 2 counts: (14 + 12 + 13) / 3
    assert full is not None and from_last is not None


def test_higher_timeframe_trend_ignores_the_unfinished_bar() -> None:
    closes = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]
    assert completed_htf_trend(closes, group=2, minute=5) == 1  # two completed groups: 4.0 > 2.0
    assert completed_htf_trend(closes, group=2, minute=1) is None


def test_paused_session_refuses_entries_until_resumed() -> None:
    session = SessionControl()
    session.pause()
    assert session.entry_allowed() is False
    session.resume()
    assert session.entry_allowed() is True


def test_replay_steps_only_while_paused_and_speed_is_positive() -> None:
    replay = ReplayControl()
    with pytest.raises(RuntimeError):
        replay.step()
    replay.pause()
    assert replay.step() == 1
    with pytest.raises(ValueError):
        replay.set_speed(0.0)
