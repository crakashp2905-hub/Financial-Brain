from __future__ import annotations

import numpy as np

from financial_brain.strategies.four_node_v2 import backtest as v2


def test_variant_is_fully_resolved_clone():
    v = v2.variant()
    assert v.executable() and v.name == "FourNode_India_v2" and v.params["target_r"] == 2.0


def test_to_15m_aggregates():
    a = np.arange(150, dtype=float).reshape(2, 75)
    o, h, lo, c = v2.to_15m(a, a + 1, a - 1, a + 0.5)
    assert o.shape == (2, 25) and o[0, 0] == 0 and h[0, 0] == 3 and lo[0, 0] == -1 and c[0, 0] == 2.5


def _flat(n=v2.BARS, px=100.0):
    return [np.full(n, px) for _ in range(4)]


def test_manage_stop_first_and_gap():
    o, h, lo, c = _flat()
    h[3], lo[3] = 105.0, 95.0
    assert v2._manage("BUY", 100.0, 98.0, 104.0, 2.0, o, h, lo, c, 3) == (98.0, "stop", 3)
    o, h, lo, c = _flat()
    o[4] = h[4] = lo[4] = c[4] = 90.0
    assert v2._manage("BUY", 100.0, 98.0, 104.0, 2.0, o, h, lo, c, 3)[:2] == (90.0, "stop")


def test_trail_locks_half_r_then_steps():
    o, h, lo, c = _flat()
    c[3] = h[3] = 102.0          # +1 R on risk 2 -> lock +0.5 R = 101
    o[4], h[4], lo[4], c[4] = 101.5, 101.6, 100.9, 101.0
    px, why, _ = v2._manage("BUY", 100.0, 98.0, 104.0, 2.0, o, h, lo, c, 3)
    assert (px, why) == (101.0, "stop")


def test_flat_data_has_no_trades():
    a = np.full((3, 75), 100.0)
    assert v2.trades_for(a, a + 0.1, a - 0.1, a, 0.0014) == []
