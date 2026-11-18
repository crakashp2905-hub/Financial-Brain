from __future__ import annotations

import numpy as np

from financial_brain.strategies.four_node_v3 import backtest as v3


def test_family_has_ten_plus_control():
    assert len(v3.STRATEGIES) == 11 and v3.STRATEGIES["CONTROL_v2"] == {}


def _flat(n=60, px=100.0):
    return [np.full(n, px) for _ in range(4)]


def test_manage_across_sessions_and_breakeven():
    fo, fh, fl, fc = _flat()
    fc[3] = fh[3] = 102.0                       # +1 R (risk 2): breakeven-only moves stop to entry
    for a in (fo, fh, fl, fc):
        a[4:30] = 101.0
    fo[30] = fh[30] = fl[30] = fc[30] = 99.0   # next session gaps below entry
    px, why = v3._manage("BUY", 100.0, 98.0, 103.0, 2.0, fo, fh, fl, fc, 3, 50, {"breakeven_only": True})
    assert (px, why) == (99.0, "stop")
    fo, fh, fl, fc = _flat()
    fc[50] = 100.7
    assert v3._manage("BUY", 100.0, 98.0, 104.0, 2.0, fo, fh, fl, fc, 3, 50, {}) == (100.7, "time")


def test_flat_data_has_no_trades_for_any_strategy():
    a = np.full((3, 75), 100.0)
    for cfg in v3.STRATEGIES.values():
        assert v3.trades_for(a, a + 0.1, a - 0.1, a, a, cfg, 0.0014, 0.0032) == []


def test_control_matches_v2_on_real_sample():
    from pathlib import Path

    import pytest

    from financial_brain.strategies.four_node_v2.backtest import trades_for as v2_trades
    p = Path("D:/Finance-Brain/data/_explore_5m/ABB.npz")
    if not p.exists():
        pytest.skip("local 5-minute data not present")
    z = np.load(p)
    a = v2_trades(z["o"], z["h"], z["l"], z["c"], 0.0014)
    b = v3.trades_for(z["o"], z["h"], z["l"], z["c"], z["v"], {}, 0.0014, 0.0032)
    assert [round(t.net, 10) for t in a] == [round(t.net, 10) for t in b]
