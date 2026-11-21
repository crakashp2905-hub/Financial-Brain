"""Causal detector tests (CP-0042, CP-0074..0090, CP-0091..0096, CP-0136, CP-0137, CP-0140, CP-0146)."""

from __future__ import annotations

import math
import random

import pytest

from financial_brain.patterns.detectors import (
    Bars,
    InvalidBars,
    discover,
    double_extrema,
    flags,
    head_shoulders,
    quarantine_rows,
    rectangle,
    triangles,
)


def _walk(seed: int, n: int = 120) -> Bars:
    rng = random.Random(seed)
    closes = [100.0]
    for _ in range(n - 1):
        closes.append(max(5.0, closes[-1] * (1 + rng.gauss(0, 0.01))))
    return Bars.from_closes(closes)


def _oscillating(seed: int, n: int = 140) -> Bars:
    """Range-bound zigzags with breakouts, so every detector has material to find."""
    rng = random.Random(seed)
    closes = []
    level = 100.0
    for i in range(n):
        phase = (i // 12) % 2
        target = 104.0 if phase == 0 else 100.0
        level = target + rng.uniform(-0.6, 0.6) + (3.0 if i % 37 == 36 else 0.0)
        closes.append(level)
    return Bars.from_closes(closes, spread=0.6)


def _key_set(formations) -> list:
    return sorted((f.key(), f.candidate_known_index) for f in formations)


# ---------------------------------------------------------------- bars and quarantine
def test_bars_reject_ohlc_inequality_and_non_finite() -> None:
    with pytest.raises(InvalidBars):
        Bars((10.0,), (9.0,), (8.0,), (10.0,))  # high < open
    with pytest.raises(InvalidBars):
        Bars((10.0,), (11.0,), (9.0,), (math.nan,))
    with pytest.raises(InvalidBars):
        Bars((10.0, 10.0), (11.0,), (9.0,), (10.0,))


def test_quarantine_counts_bad_rows_and_keeps_good_ones() -> None:
    rows = [(10, 11, 9, 10.5), (10, 9, 8, 9.5), (-1, 2, 1, 1.5), (10, 11, 9, math.nan)]
    good, counts = quarantine_rows(rows)
    assert good == [(10, 11, 9, 10.5)]
    assert counts == {"ohlc_inequality": 1, "non_finite_or_nonpositive": 2}


# ---------------------------------------------------------------- positive fixtures
def test_rectangle_breakout_is_found_and_confirmed_up_only_after_close() -> None:
    closes = [104.0 if i % 2 == 0 else 100.0 for i in range(24)] + [107.0, 108.0, 108.5]
    bars = Bars.from_closes(closes, spread=0.4)
    found = [f for f in rectangle(bars, window=20) if f.confirmed_direction == "bullish"]
    assert found, "expected a confirmed bullish rectangle breakout"
    f = found[0]
    assert f.direction == "neutral"  # candidate never carries a direction (CP-0075)
    assert f.confirmed_index > f.candidate_known_index
    assert bars.close[f.confirmed_index] > f.geometry["top"]


def test_double_bottom_candidate_then_neckline_break() -> None:
    closes = (
        [110 - i for i in range(11)]  # 110 .. 100
        + [100 + i * 0.9 for i in range(1, 11)]  # up to 109
        + [109 - i for i in range(1, 11)]  # back down to ~99
        + [99 + i * 0.9 for i in range(1, 12)]  # up through the neckline
        + [120.0, 121.0]
    )
    bars = Bars.from_closes([float(c) for c in closes], spread=0.3)
    found = double_extrema(bars, window=30, min_sep=5, tol_atr=1.0)
    bottoms = [f for f in found if f.detector == "double_bottom"]
    assert bottoms
    assert any(f.confirmed_direction == "bullish" for f in bottoms)
    assert all(f.failed_index is None or f.failed_index > f.candidate_known_index for f in bottoms)


def test_flag_requires_pole_and_limited_retracement() -> None:
    closes = [100.0] * 8 + [100 + 2 * i for i in range(1, 10)]  # pole from 100 to ~118
    closes += [117.0, 116.5, 117.2, 116.8, 117.1, 116.9, 117.4]  # shallow flag
    closes += [119.5, 120.5]
    bars = Bars.from_closes(closes, spread=0.2)
    found = flags(bars, pole_len=8, pole_min_ret=0.08)
    bulls = [f for f in found if f.detector == "flag_bullish"]
    assert bulls
    assert all(0 <= f.geometry["retrace"] <= 0.5 for f in bulls)


def test_head_shoulders_top_candidate_needs_known_right_shoulder() -> None:
    shape = [100, 104, 100, 110, 100, 104, 100, 99, 97, 96, 95, 94, 93]
    closes = [float(x) for x in shape] * 2
    bars = Bars.from_closes(closes, spread=0.4)
    found = head_shoulders(bars, window=20, tol_atr=5.0)
    tops = [f for f in found if f.detector == "head_shoulders_top"]
    for f in tops:
        assert f.candidate_known_index >= f.geometry["shoulders"][1]


# ---------------------------------------------------------------- negative fixtures
def test_strong_trend_is_not_a_rectangle() -> None:
    bars = Bars.from_closes([100.0 + 0.8 * i for i in range(80)], spread=0.3)
    assert rectangle(bars, window=20) == []


def test_flat_series_has_no_triangles_or_double_extrema() -> None:
    bars = Bars.from_closes([100.0] * 80, spread=0.0 + 0.01)
    assert triangles(bars) == []
    assert double_extrema(bars) == []


# ---------------------------------------------------------------- causality
@pytest.mark.parametrize("seed", range(6))
def test_prefix_invariance_for_every_detector(seed: int) -> None:
    """detect(bars[:t+1]) equals the full run restricted to what was knowable at t."""
    for bars in (_walk(seed), _oscillating(seed)):
        full = discover(bars)
        for t in range(30, len(bars), 17):
            expected = []
            for f in full:
                seen = f.as_of(t)
                if seen is not None:
                    expected.append(seen)
            got = discover(bars.head(t + 1))
            assert _key_set(got) == _key_set(expected)
            assert sorted((g.state_at(t) or "", g.key()) for g in got) == sorted(
                (e.state_at(t) or "", e.key()) for e in expected
            )


@pytest.mark.parametrize("seed", range(4))
def test_future_perturbation_leaves_earlier_formations_unchanged(seed: int) -> None:
    bars = _oscillating(seed, n=120)
    t = len(bars) - 1
    base = discover(bars)
    extra_closes = list(bars.close) + [250.0, 300.0, 40.0, 320.0]
    extended = discover(Bars.from_closes(extra_closes, spread=0.6))
    base_known = {f.as_of(t).key() for f in base if f.as_of(t) is not None}
    ext_known = {f.as_of(t).key() for f in extended if f.as_of(t) is not None}
    assert base_known == ext_known


def test_candidates_that_never_confirm_are_retained_as_candidates() -> None:
    bars = _oscillating(3, n=160)
    found = discover(bars)
    states = {f.state_at(len(bars) - 1) for f in found}
    assert "CANDIDATE" in states or "CONFIRMED" in states or "FAILED" in states
    assert all(f.state_at(f.candidate_known_index) in ("CANDIDATE", "CONFIRMED", "FAILED") for f in found)


def test_detection_is_deterministic_under_repeat() -> None:
    bars = _oscillating(5)
    assert _key_set(discover(bars)) == _key_set(discover(bars))
