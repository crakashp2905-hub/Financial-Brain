"""Zoo edge strategies: market-neutral deciles, the turn-of-month window, every candidate carries its verdict."""
from datetime import date

import numpy as np
import pandas as pd

from financial_brain.paper import zoo as Z
from financial_brain.paper import zoo_edges as ZE


def closes(n_names=60, n=300, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2025-01-01", periods=n).date
    return pd.DataFrame({f"L{i}": 100 * np.cumprod(1 + rng.normal(0.0005 * (i - 30) / 30, 0.01, n)) for i in range(n_names)}, index=idx)


def test_long_short_targets_are_market_neutral_deciles():
    C = closes()
    for kind in ("W1", "W2", "W3"):
        w = ZE.ls_targets(C, list(C.columns), kind)
        assert abs(sum(w.values())) < 1e-9
        assert sum(v for v in w.values() if v > 0) == np.float64(1.0) or abs(sum(v for v in w.values() if v > 0) - 1) < 1e-9
        assert 10 <= len(w) <= 14


def test_turn_of_month_window():
    assert ZE.z1_exposure(date(2026, 10, 28)) == 1.0        # two sessions before 30 Oct (Fri) by weekdays
    assert ZE.z1_exposure(date(2026, 10, 30)) == 1.0
    assert ZE.z1_exposure(date(2026, 11, 3)) == 1.0         # held to the 3rd session's close
    assert ZE.z1_exposure(date(2026, 11, 4)) == 0.0
    assert ZE.z1_exposure(date(2026, 10, 15)) == 0.0


def test_every_edge_strategy_is_kinded_and_carries_a_verdict():
    edges = [k for k, v in Z.KIND.items() if v == "edge"]
    assert len(edges) == 16 and all(k in ZE.VERDICT for k in edges)
