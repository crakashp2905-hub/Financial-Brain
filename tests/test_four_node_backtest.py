from __future__ import annotations

import numpy as np
import pytest
from datetime import date, timedelta

from financial_brain.strategies.four_node import backtest as bt


def test_all_128_variants_distinct():
    vs = bt.all_variants()
    assert len(vs) == 128 and len({bt.variant_code(v) for v in vs}) == 128


def _flat(price=100.0, days=3):
    a = np.full((days, bt.BARS), price)
    return a.copy(), a + 0.1, a - 0.1, a.copy()


def test_simulate_stop_first_when_both_touched():
    o, h, lo, c = (np.full(bt.BARS, 100.0) for _ in range(4))
    h[5], lo[5] = 104.0, 96.0
    p = {"trail_start_atr": 99.0, "trail_step_atr": 1.0}
    px = bt._simulate("BUY", 100.0, 97.0, 103.0, 1.0, o, h, lo, c, 5, 1, 1, 1, list(h), list(lo), 99, p)
    assert px == 97.0


def test_simulate_gap_through_stop_fills_open_and_flattens():
    o, h, lo, c = (np.full(bt.BARS, 100.0) for _ in range(4))
    o[6] = h[6] = lo[6] = c[6] = 95.0
    p = {"trail_start_atr": 99.0, "trail_step_atr": 1.0}
    assert bt._simulate("BUY", 100.0, 97.0, 103.0, 1.0, o, h, lo, c, 5, 1, 1, 1, list(h), list(lo), 99, p) == 95.0
    o2, h2, l2, c2 = (np.full(bt.BARS, 100.0) for _ in range(4))
    c2[bt.FLATTEN_BAR] = 100.5
    assert bt._simulate("BUY", 100.0, 97.0, 103.0, 1.0, o2, h2, l2, c2, 5, 1, 1, 1, list(h2), list(l2), 99,
                        p) == 100.5


def test_takes_respects_choices():
    ev = bt.Event(1, 10, "BUY", "EXC_LITERAL", True, True, True, False, True, False, True, False, True,
                  False, True, True, {(0, 0): (0.01, 1.0), (0, 1): (0, 0), (1, 0): (0, 0), (1, 1): (0, 0)})
    v = {a: alts[0] for a, alts in zip(bt.CHOICE_IDS, [x.alternatives for x in bt.AMBIGUITIES], strict=True)}
    assert bt.takes(ev, v)
    assert not bt.takes(ev, {**v, "BUY_EXCEPTION_SIDE": "MIRROR_ABOVE"})
    assert not bt.takes(ev, {**v, "RETURN_CANDLE": "NO_OPPOSITE_CANDLE_AT_BREAKOUT"})
    assert not bt.takes(ev, {**v, "FIB_ANCHOR": "PREV_DAY_HIGH_TO_LOW"})
    assert bt.outcome(ev, v) == (0.01, 1.0)


def test_events_on_flat_data_is_empty():
    assert bt.events_for(*_flat(), rt_cost=0.0014,
                         days=["2026-11-20", "2026-11-23", "2026-11-24"]) == []


def test_event_runner_requires_and_attaches_completed_session_fib_timestamps():
    o, h, lo, c = _flat(days=3)
    with pytest.raises(TypeError):
        bt.events_for(o, h, lo, c, rt_cost=0.0014)  # type: ignore[call-arg]
    with pytest.raises(ValueError, match="strictly increasing"):
        bt.events_for(o, h, lo, c, rt_cost=0.0014,
                      days=["2026-11-20", "2026-11-20", "2026-11-24"])

    rng = np.random.default_rng(124)
    sessions, bars = 40, bt.BARS
    close = 100 + np.cumsum(rng.normal(0, 0.8, (sessions, bars)), axis=1)
    open_ = close + rng.normal(0, 0.2, (sessions, bars))
    high = np.maximum(open_, close) + rng.uniform(0.1, 0.8, (sessions, bars))
    low = np.minimum(open_, close) - rng.uniform(0.1, 0.8, (sessions, bars))
    session_days = []
    day = date(2024, 1, 1)
    while len(session_days) < sessions:
        if day.weekday() < 5:
            session_days.append(day.isoformat())
        day += timedelta(days=1)
    events = bt.events_for(open_, high, low, close, rt_cost=0.0014, days=session_days)
    assert events
    for event in events:
        assert event.fib_snapshots is not None
        for snapshot in event.fib_snapshots.values():
            assert snapshot.version == "four-node-daily-fib-v1"
            assert snapshot.anchor_session_id == f"NSE:{session_days[event.day - 1]}"
            assert snapshot.anchor_close_at < snapshot.evaluated_session_open_at


def test_every_grid_variant_is_executable():
    from financial_brain.strategies.four_node.spec import india_variant
    assert all(india_variant(v, owner="owner-grid-2026-11-18").executable() for v in bt.all_variants())
