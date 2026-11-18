from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from financial_brain.signals.desk_logic import (
    DeskError,
    FrozenExitPolicy,
    exit_guard,
    hit_probabilities,
    overlap,
    price_constraints,
    rotation,
    simulate_sip,
    swing_candidates,
)

T = datetime(2026, 11, 17, 4, 0, tzinfo=timezone.utc)
ATR = FrozenExitPolicy("atr-v1", "ATR", stop_mult=1.5, target_mult=3.0)


def test_exit_policy_levels():
    assert ATR.levels(100, 2) == (97.0, 106.0)
    with pytest.raises(DeskError):
        FrozenExitPolicy("f", "FIXED").levels(100, 2)
    with pytest.raises(DeskError):
        ATR.levels(100, 0)


def test_hit_probabilities_sum_and_gap():
    p = hit_probabilities(100, 97, 106, 0.015, 10, 0.0, 0.0)
    assert p["p_target"] + p["p_stop"] + p["p_time"] == pytest.approx(1)
    g = hit_probabilities(100, 97, 106, 0.015, 10, 0.05, 0.08)
    assert g["p_stop"] > p["p_stop"] and g["mean_gap_beyond_stop"] > 0
    with pytest.raises(DeskError):
        hit_probabilities(100, 101, 106, 0.01, 5, 0, 0)


def test_price_constraints():
    ok = price_constraints(entry_low=99, entry_high=100, bid=99.9, ask=100.0, max_spread_bps=20,
                           max_slip_bps=10, decided_at=T, ttl=timedelta(minutes=5), now=T)
    assert ok == []
    bad = price_constraints(entry_low=99, entry_high=100, bid=100.5, ask=101.5, max_spread_bps=20,
                            max_slip_bps=10, decided_at=T, ttl=timedelta(minutes=5), now=T + timedelta(minutes=6))
    assert len(bad) == 3


def test_swing_candidates_can_be_empty_and_cap_five():
    rows = [dict(symbol=f"S{i}", score=i, close=100, atr=2, p_target=0.45, p_stop=0.4) for i in range(8)]
    assert len(swing_candidates(rows, ATR, cost=0.5)) == 5
    weak = [dict(symbol="W", score=1, close=100, atr=2, p_target=0.2, p_stop=0.7)]
    assert swing_candidates(weak, ATR, cost=0.5) == []


def test_rotation_equal_vol_and_cash():
    w = rotation({"EQ": 0.2, "GOLD": 0.1, "DEBT": 0.05}, {"EQ": 1, "GOLD": 1, "DEBT": -1}, 0.1)
    assert w["GOLD"] == pytest.approx(2 * w["EQ"]) and "DEBT" not in w and w["CASH"] >= 0
    assert rotation({"EQ": 0.2}, {"EQ": -1}, 0.1) == {"CASH": 1.0}


def test_sip_simulation_respects_lag_and_missing_nav():
    navs = {date(2026, 1, 2): 10.0, date(2026, 2, 3): 12.5, date(2026, 3, 2): 10.0}
    r = simulate_sip(navs, [date(2026, 1, 1), date(2026, 2, 1), date(2026, 4, 1)], 1000, 1)
    assert r["fills"][0][1] == date(2026, 1, 2) and r["fills"][2][1] is None
    assert r["invested"] == 2000 and r["units"] == pytest.approx(100 + 80)


def test_overlap_and_exit_guard():
    o = overlap([("RELIANCE", 100, ("NIFTY", "ENERGY")), ("NIFTYBEES", 200, ("NIFTY",))])
    assert o == {"NIFTY": 300, "ENERGY": 100}
    cuts = exit_guard({"A": 50, "B": 30, "C": 20}, 70)
    assert cuts == [("A", 0.6)]
    assert exit_guard({"A": 10}, 70) == []
