"""Causal pivot tests (CP-0056, CP-0057, CP-0136, CP-0138, CP-0072)."""

from __future__ import annotations

import random

import pytest

from financial_brain.patterns.pivots import causal_pivots, known_pivots


def test_pivot_known_only_after_right_window_arrives() -> None:
    highs = [1, 2, 9, 3, 2, 1, 1]
    lows = [h - 0.5 for h in highs]
    pivots = causal_pivots(highs, lows, left=2, right=2)
    high_piv = [p for p in pivots if p.kind == "high"]
    assert [(p.index, p.known_at_index) for p in high_piv] == [(2, 4)]
    # Prefix ending at index 3 cannot yet know the pivot at index 2.
    assert known_pivots(causal_pivots(highs[:4], lows[:4], left=2, right=2), 3) == []
    assert known_pivots(causal_pivots(highs[:5], lows[:5], left=2, right=2), 4)


def test_flat_top_resolves_to_first_bar_deterministically() -> None:
    highs = [1, 2, 5, 5, 2, 1, 1]
    lows = [0.0] * len(highs)
    piv = [p for p in causal_pivots(highs, lows, left=2, right=2) if p.kind == "high"]
    assert [p.index for p in piv] == [2]


def test_constant_series_yields_no_pivots() -> None:
    assert causal_pivots([3.0] * 20, [3.0] * 20, left=2, right=2) == []


def test_invalid_inputs_fail_closed() -> None:
    with pytest.raises(ValueError):
        causal_pivots([1, float("nan"), 2, 1, 1], [0] * 5)
    with pytest.raises(ValueError):
        causal_pivots([1, 2], [1], left=1, right=1)
    with pytest.raises(ValueError):
        causal_pivots([1, 2, 3], [1, 2, 3], left=0, right=1)


@pytest.mark.parametrize("seed", range(40))
def test_prefix_invariance_on_random_series(seed: int) -> None:
    """Output at time t equals full-run output restricted to pivots known by t."""
    rng = random.Random(seed)
    n = 60
    closes = [100.0]
    for _ in range(n - 1):
        closes.append(max(1.0, closes[-1] + rng.gauss(0, 1.5)))
    highs = [c + rng.random() for c in closes]
    lows = [c - rng.random() for c in closes]
    full = causal_pivots(highs, lows, left=2, right=3)
    for t in range(5, n):
        prefix = causal_pivots(highs[: t + 1], lows[: t + 1], left=2, right=3)
        assert known_pivots(prefix, t) == known_pivots(full, t)
        assert all(p.known_at_index <= t for p in prefix)


def test_future_spike_does_not_alter_earlier_pivots() -> None:
    rng = random.Random(7)
    highs = [100 + rng.random() for _ in range(40)]
    lows = [h - 1 for h in highs]
    base = known_pivots(causal_pivots(highs, lows), 30)
    highs2 = highs + [500.0, 600.0, 700.0]
    lows2 = lows + [499.0, 599.0, 699.0]
    extended = known_pivots(causal_pivots(highs2, lows2), 30)
    assert base == extended
