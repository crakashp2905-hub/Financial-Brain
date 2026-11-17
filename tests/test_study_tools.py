from __future__ import annotations

import pytest

from financial_brain.evaluation.study_tools import (
    CostModel,
    Registration,
    Telemetry,
    benjamini_hochberg,
    cross_index_features,
    decompose,
    direction_and_pnl,
    excess_vs_matched,
    holm,
    ma_trend,
    mean_reversion,
    opening_range_breakout,
    peer_percentile,
    persistence,
    random_matched,
    replay_parity,
    short_horizon_plausible,
    stable,
    stress_pnl,
    vol_scaled_size,
)
from financial_brain.strategies.four_node.nodes import pivots


def test_registration_gate():
    r = Registration("S1", "h", ("A",), (("n", 20.0),), "c1", "lb>0", "2026-11-17")
    assert not r.runnable()
    assert Registration(**{**r.__dict__, "ledger_entry": "L-1"}).runnable()


def test_rule_signals():
    up = [float(i) for i in range(30)]
    assert ma_trend(up, 3, 10)[-1] == 1 and ma_trend(up, 3, 10)[0] == 0
    xs = [10.0] * 19 + [5.0]
    assert mean_reversion(xs, 20, 2.0)[-1] == 1
    assert vol_scaled_size(0.1, 0.2, 1.0) == 0.5 and vol_scaled_size(0.1, 0, 1) == 0


def test_short_horizon_gate():
    assert not short_horizon_plausible(10, 5, 1, True)
    assert short_horizon_plausible(20, 5, 1, True) and not short_horizon_plausible(20, 5, 1, False)


def test_decompose_and_matched_excess():
    d = decompose(0.10, 0.11, 0.12)
    assert d["friction"] == pytest.approx(-0.01) and d["tracking"] == pytest.approx(-0.01)
    assert excess_vs_matched([0.02, 0.0], [0.01, 0.01], 2.0) == pytest.approx(-0.01)


def test_stress():
    assert stress_pnl(100000, 10, "liquidity_collapse") == pytest.approx(-2000 - 1000)


def test_persistence_and_peers():
    assert persistence({"a": 1, "b": 2, "c": 3}, {"a": 1, "b": 5, "c": 2}, 2) == 0.5
    assert peer_percentile(5, [1, 5, 9, 10]) == 0.5


def test_baselines():
    r = random_matched(100, 10, 1)
    assert sum(r) == 10 and r == random_matched(100, 10, 1)
    assert opening_range_breakout([2, 3, 4, 5], [1, 1, 1, 0], [2, 2, 5, 0.5], 2) == [0, 0, 1, -1]


def test_cost_model():
    assert CostModel(5, 1, 2, 1).round_trip_bps() == 11


def test_multiple_testing():
    p = {"a": 0.001, "b": 0.02, "c": 0.04, "d": 0.5}
    assert holm(p, 0.05) == {"a"}
    assert benjamini_hochberg(p, 0.1) == {"a", "b", "c"}


def test_stability():
    assert stable([0.1, 0.2, -0.1, 0.3], min_share_positive=0.7)
    assert not stable([0.5, -0.1, -0.2, -0.3], min_share_positive=0.7)


def test_direction_and_pnl_scored_separately():
    r = direction_and_pnl(["UP", "UP", "DOWN"], ["UP", "DOWN", "DOWN"], [10, -30, 0])
    assert r["balanced_accuracy"] == pytest.approx(0.75) and r["net_pnl"] == -20


def test_cross_index_features():
    f = cross_index_features([0.01, 0.02], [0.0, 0.005], 2)
    assert f["divergence"] == pytest.approx(0.015)


def test_telemetry():
    t = Telemetry([10, 20, 500], expired=1, missed=1, duplicates=0, issued=10, covered_bars=24,
                  expected_bars=25)
    s = t.summary()
    assert s["p99_ms"] == 500 and s["expiry_rate"] == 0.1 and s["coverage"] == 0.96


def test_pivot_stream_replay_parity():
    highs = [1, 2, 5, 2, 1, 3, 1, 1, 6, 2, 1]

    def batch(h):
        return [(p.side, p.pivot_at) for p in pivots(list(h), [x - 0.5 for x in h], 2, 2)]

    assert replay_parity(batch, batch, highs)
