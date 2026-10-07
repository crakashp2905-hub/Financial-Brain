"""The live paper zoo: settlement arithmetic, idempotence, pairs and martingale state."""
from datetime import date

import numpy as np
import pandas as pd
import pytest

from financial_brain.paper import zoo as Z


def test_settle_earns_yesterdays_weights_and_charges_the_change(tmp_path):
    z = Z.connect(tmp_path / "z.duckdb")
    d0, d1 = date(2026, 10, 7), date(2026, 10, 8)
    Z.settle(z, "s", d0, None, {}, 0.0, {"A": 0.5, "B": 0.5})
    r = Z.settle(z, "s", d1, d0, {"A": 0.02, "B": -0.01}, 0.004, {"A": 1.0})
    assert r["gross"] == pytest.approx(0.5 * 0.02 - 0.5 * 0.01)
    assert r["cost"] == pytest.approx(1.0 * Z.COST_LONG)                      # +0.5 A, -0.5 B
    assert r["net"] == pytest.approx(r["gross"] - r["cost"]) and r["bench"] == 0.004




def test_pairs_open_on_divergence_and_close_on_crossing():
    rng = np.random.default_rng(0)
    n = 400
    a = 100 * np.cumprod(1 + rng.normal(0, 0.01, n))
    b = a * (1 + rng.normal(0, 0.002, n))
    others = {f"X{k}": 100 * np.cumprod(1 + rng.normal(0, 0.02, n)) for k in range(5)}
    px = pd.DataFrame({"A": a, "B": b, **others})
    px.iloc[-1, 0] = px.iloc[-1, 0] * 1.10                                    # A jumps away from its twin
    w, st = Z.pairs_targets(px, {}, coint=False, n_pairs=1)
    assert w and w.get("A", 0) < 0 < w.get("B", 0)                            # short the rich leg, long the cheap one
    assert st["pairs"][0]["pos"] == -1


def test_martingale_state_doubles_after_a_loss(tmp_path):
    z = Z.connect(tmp_path / "z.duckdb")
    lv = pd.Series(np.linspace(100, 120, 300))
    vix = pd.Series(np.full(300, 14.0))
    Z.index_targets(lv, vix, z, date(2026, 10, 7))
    falling = pd.concat([lv, pd.Series([lv.iloc[-1] * 0.99])], ignore_index=True)
    Z.index_targets(falling, pd.concat([vix, pd.Series([14.0])], ignore_index=True), z, date(2026, 10, 8))
    st = Z._state(z, "martingale")
    assert st["stake"] == pytest.approx(0.04) and not st["ruined"]
    assert Z._state(z, "anti_martingale")["stake"] == pytest.approx(0.02)
