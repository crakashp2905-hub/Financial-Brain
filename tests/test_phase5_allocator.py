import numpy as np
import pytest

from financial_brain.risk import allocator as A


def series(mean, sd, n, seed=0):
    return tuple(np.random.default_rng(seed).normal(mean, sd, n))


def test_short_history_is_paper_with_no_capital():
    r = A.allocate([A.Sleeve("new", series(0.02, 0.03, 10), True)])
    assert r["detail"]["new"]["state"] == A.PAPER and r["weights"]["new"] == 0


def test_real_edge_gets_capital_when_the_money_gate_passed():
    r = A.allocate([A.Sleeve("good", series(0.02, 0.03, 120), True)])
    assert r["detail"]["good"]["state"] == A.ACTIVE and r["weights"]["good"] > 0


def test_a_statistically_perfect_sleeve_still_gets_nothing_without_the_gate():
    r = A.allocate([A.Sleeve("good", series(0.02, 0.03, 120), False)])
    assert r["weights"]["good"] == 0 and "good" in r["paper_only"]
    assert r["detail"]["good"]["held_to_paper_by_money_gate"]


def test_decayed_edge_is_stopped():
    r = np.r_[series(0.02, 0.03, 80, 1), series(-0.01, 0.03, 40, 2)]
    out = A.allocate([A.Sleeve("fading", tuple(r), True)])
    assert out["detail"]["fading"]["state"] in (A.STOP, A.REDUCE)
    assert out["detail"]["fading"]["t"] < A.decayed_edge(r[:80])["t"]


def test_no_edge_is_stopped():
    r = A.allocate([A.Sleeve("noise", series(0.0, 0.03, 120, 5), True)])
    assert r["weights"]["noise"] == 0


def test_weights_never_exceed_one_or_the_cap():
    sl = [A.Sleeve(f"s{i}", series(0.04, 0.02, 120, i), True) for i in range(6)]
    r = A.allocate(sl)
    assert sum(r["weights"].values()) <= 1 + 1e-9
    assert max(r["weights"].values()) <= A.MAX_SLEEVE + 1e-9


def test_effective_sample_is_smaller_than_the_count():
    e = A.decayed_edge(series(0.01, 0.03, 200))
    assert e["n_eff"] < e["n"]


def test_errors():
    with pytest.raises(A.AllocatorError):
        A.allocate([])
    with pytest.raises(A.AllocatorError):
        A.allocate([A.Sleeve("a", (0.1,) * 30), A.Sleeve("a", (0.1,) * 30)])
