"""The live paper zoo: settlement arithmetic, idempotence, pairs and martingale state."""
from datetime import date

import numpy as np
import pandas as pd
import pytest

from financial_brain.paper import zoo as Z
from financial_brain.paper import zoo_edges as ZE


def test_settle_earns_yesterdays_weights_and_charges_the_change(tmp_path):
    z = Z.connect(tmp_path / "z.duckdb")
    d0, d1 = date(2026, 10, 7), date(2026, 10, 8)
    Z.settle(z, "s", d0, None, {}, 0.0, {"A": 0.5, "B": 0.5})
    r = Z.settle(z, "s", d1, d0, {"A": 0.02, "B": -0.01}, 0.004, {"A": 1.0})
    assert r["gross"] == pytest.approx(0.5 * 0.02 - 0.5 * 0.01)
    assert r["cost"] == pytest.approx(1.0 * Z.COST_LONG)                      # +0.5 A, -0.5 B
    assert r["net"] == pytest.approx(r["gross"] - r["cost"]) and r["bench"] == 0.004


def test_monthly_rebalance_audit_reports_exact_missed_signal_date_and_hash_mismatch():
    scheduled = date(2026, 11, 2)
    state = {"set": "2026-10-30", "targets": {"edge_p1_residual_mom": {"OLD": 1.0}}}
    intended = {"edge_p1_residual_mom": {"NEW": 1.0}}

    audit = ZE.monthly_rebalance_audit(date(2026, 11, 4), scheduled, state, intended)

    assert audit["status"] == "MISSED_OR_STALE"
    assert audit["signal_date"] == "2026-11-02"
    assert audit["detected_on"] == "2026-11-04"
    assert "edge_p1_residual_mom" in audit["mismatched_books"]
    assert len(audit["intended_hashes"]["edge_p1_residual_mom"]) == 64
    assert ZE.monthly_rebalance_audit(scheduled, scheduled, state, intended) is None
    assert state["targets"]["edge_p1_residual_mom"] == {"OLD": 1.0}


def test_missing_held_return_is_incomplete_and_does_not_block_other_strategy(tmp_path):
    z = Z.connect(tmp_path / "missing-return.duckdb")
    first, second = date(2026, 11, 2), date(2026, 11, 3)
    Z.settle(z, "missing", first, None, {}, 0.0, {"A": 1.0})
    Z.settle(z, "observed", first, None, {}, 0.0, {"B": 1.0})

    missing = Z.settle(z, "missing", second, first, {}, 0.0, {"A": 1.0})
    observed = Z.settle(z, "observed", second, first, {"B": 0.02}, 0.01, {"B": 1.0})

    assert missing["gross"] is None and missing["net"] is None and missing["bench"] is None
    assert observed["gross"] == pytest.approx(0.02)
    assert observed["net"] == pytest.approx(observed["gross"] - observed["cost"])
    quality = z.execute(
        "SELECT status, missing_return_keys FROM zoo_pnl_quality WHERE strategy='missing' AND day=?",
        [second],
    ).fetchone()
    assert quality == ("INCOMPLETE_MISSING_RETURN", '["A"]')
    summary = Z.summary(z)
    assert pd.isna(summary.loc["missing", "last_net"])
    assert summary.loc["observed", "last_net"] == pytest.approx(observed["net"])




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


def test_the_carry_book_enters_rich_bases_and_earns_spot_minus_future():
    day = date(2026, 10, 7)
    snap = pd.DataFrame({"symbol": ["RICH", "THIN", "LATE"], "spot": [100.0, 100.0, 100.0], "fut": [101.0, 100.2, 101.0],
                         "fut_expiry": ["2026-10-27", "2026-10-27", "2026-10-09"], "basis_annualised": [0.18, 0.04, 0.18]})
    w, st = Z.carry_targets(snap, {}, day)
    assert set(w) == {"CARRY:RICH"} and st["pos"]["RICH"] == "2026-10-27"      # LATE has too few days left
    nxt = snap.assign(spot=[101.0, 100.0, 100.0], fut=[101.6, 100.2, 101.0])
    r = Z.carry_returns(snap, nxt)
    assert r["CARRY:RICH"] == pytest.approx((1.0 - 0.6) / 100)                   # the basis narrowed by 0.4
    w2, st2 = Z.carry_targets(nxt, st, date(2026, 10, 28))
    assert "CARRY:RICH" not in w2                                                  # held to expiry, then gone


def test_sharpe_allocator_weights_by_trailing_sharpe_and_earns_them(tmp_path):
    z = Z.connect(tmp_path / "z.duckdb")
    rng = np.random.default_rng(1)
    months = pd.period_range("2022-01", "2026-09", freq="M")
    panel = pd.DataFrame({"carry: basis > 10%": 0.0075 + rng.normal(0, 0.001, len(months)),   # high Sharpe
                          "rule: macd_cross": -0.002 + rng.normal(0, 0.05, len(months)),        # below cash
                          "nifty: grid": 0.006 + rng.normal(0, 0.02, len(months))}, index=months)
    w = Z.allocator_weights(z, date(2026, 10, 7), panel)
    assert set(w) <= {"carry_basis_10", "grid"} and w["carry_basis_10"] > w.get("grid", 0)
    assert sum(w.values()) == pytest.approx(1.0)
    d0, d1 = date(2026, 10, 7), date(2026, 10, 8)
    for s, n in (("carry_basis_10", 0.001), ("grid", 0.004)):
        z.execute("INSERT INTO zoo_pnl VALUES (?,?,0,0,0,0,1)", [s, d0])
        z.execute("INSERT INTO zoo_targets VALUES (?,?,?,1.0)", [s, d0, "X"])           # fully invested: no cash interest
        z.execute("INSERT INTO zoo_pnl VALUES (?,?,?,0,?,0,1)", [s, d1, n, n])
    r0 = Z.run_allocator(z, d0, None, panel)
    assert r0["cost"] == pytest.approx(Z.ALLOC_COST) and r0["net"] == pytest.approx(-Z.ALLOC_COST)   # buys in once
    r1 = Z.run_allocator(z, d1, d0, panel)
    assert r1["cost"] == 0.0                                                 # same month: no rebalance
    assert r1["gross"] == pytest.approx(w["carry_basis_10"] * 0.001 + w.get("grid", 0) * 0.004)
    assert r1["bench"] == pytest.approx(0.0025)
