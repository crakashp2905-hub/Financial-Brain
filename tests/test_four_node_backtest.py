from __future__ import annotations

import numpy as np

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
    assert bt.events_for(*_flat(), rt_cost=0.0014) == []


def test_every_grid_variant_is_executable():
    from financial_brain.strategies.four_node.spec import india_variant
    assert all(india_variant(v, owner="owner-grid-2026-11-18").executable() for v in bt.all_variants())
