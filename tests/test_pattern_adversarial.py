"""Adversarial, fuzz, golden and restart tests (CP-0139, CP-0142, CP-0143, CP-0144, CP-0145, CP-0146, CP-0152)."""

from __future__ import annotations

import math
import random
from datetime import date

import pytest

from financial_brain.patterns.candles import DailyBar, final_weeks, weekly_bars
from financial_brain.patterns.detectors import (
    Bars,
    InvalidBars,
    discover,
    formation_from_dict,
    formation_to_dict,
    quarantine_rows,
    rectangle,
)
from financial_brain.patterns.marketdata import adjust_for_dividend
from financial_brain.patterns.ranking import resolve_overlaps


def _series(kind: str, n: int = 120) -> Bars:
    seeds = {"flat": 1, "noisy": 2, "gapping": 3, "discontinuous": 4}  # fixed: hash() is per-process
    rng = random.Random(seeds[kind])
    if kind == "flat":
        closes = [50.0] * n
    elif kind == "noisy":
        closes = [100 + rng.gauss(0, 5) for _ in range(n)]
        closes = [max(1.0, c) for c in closes]
    elif kind == "gapping":
        closes = [100.0 + (30.0 if i % 17 == 16 else 0.0) + 0.1 * i for i in range(n)]
    elif kind == "discontinuous":
        closes = [100.0 if i < 60 else 400.0 for i in range(n)]
    else:
        raise ValueError(kind)
    return Bars.from_closes(closes, spread=0.4)


@pytest.mark.parametrize("kind", ["flat", "noisy", "gapping", "discontinuous"])
def test_adversarial_volatility_regimes_do_not_crash_or_emit_nan(kind: str) -> None:
    bars = _series(kind)
    found = discover(bars)
    for f in found:
        assert f.candidate_known_index < len(bars)
        for value in f.geometry.values():
            if isinstance(value, float):
                assert math.isfinite(value)


def test_ohlc_fuzzing_routes_every_bad_row_to_quarantine() -> None:
    rng = random.Random(42)
    rows = []
    for _ in range(300):
        o, c = rng.uniform(1, 100), rng.uniform(1, 100)
        h = rng.uniform(1, 100)  # often below open or close
        lo = rng.uniform(1, 100)
        rows.append((o, h, lo, c))
    good, counts = quarantine_rows(rows)
    bad = len(rows) - len(good)
    assert bad == sum(counts.values())
    for o, h, lo, c in good:
        Bars((o,), (max(h, lo, o, c),), (min(h, lo, o, c),), (c,))  # valid rows construct
    assert bad > 0


def test_ohlc_fuzz_rejects_invalid_bars_with_typed_error() -> None:
    with pytest.raises(InvalidBars):
        Bars((1.0,), (0.5,), (0.4,), (0.6,))


def test_golden_rectangle_case_regression_oracle() -> None:
    """Regression oracle from a fixed fixture; a changed value means the detector behaviour changed."""
    closes = [104.0 if i % 2 == 0 else 100.0 for i in range(24)] + [107.0, 108.0, 108.5]
    bars = Bars.from_closes(closes, spread=0.4)
    found = rectangle(bars, window=20)
    assert found
    first = found[0]
    assert first.candidate_known_index == 19
    assert first.geometry["top"] == pytest.approx(104.4)
    assert first.geometry["bottom"] == pytest.approx(99.6)
    assert first.confirmed_index == 24
    assert first.confirmed_direction == "bullish"


def test_formation_round_trip_preserves_every_view() -> None:
    bars = _series("noisy", n=140)
    for f in discover(bars):
        restored = formation_from_dict(formation_to_dict(f))
        assert restored == f
        for t in (f.candidate_known_index, len(bars) - 1):
            assert restored.state_at(t) == f.state_at(t)


def test_overlapping_shapes_on_same_pivots_keep_one_by_precedence() -> None:
    from financial_brain.patterns.detectors import Formation

    a = Formation("rectangle", "1", 0, 10, geometry={"pivots": [3, 7]})
    b = Formation("double_bottom", "1", 0, 10, geometry={"pivots": [3, 7]})
    c = Formation("flag_bullish", "1", 0, 12, geometry={"pivots": [9]})
    kept = resolve_overlaps([a, b, c])
    names = sorted(f.detector for f in kept)
    assert names == ["double_bottom", "flag_bullish"]


def test_dividend_adjustment_removes_the_fake_ex_date_drop() -> None:
    raw = [100.0, 100.0, 97.0, 97.5]  # ex-date drop of the dividend only
    adjusted = adjust_for_dividend(raw, ex_index=2, amount=3.0)
    assert adjusted[1] == pytest.approx(97.0)
    assert abs(adjusted[2] - adjusted[1]) < 1e-9
    with pytest.raises(ValueError):
        adjust_for_dividend(raw, ex_index=2, amount=200.0)


def test_only_final_weeks_feed_signals() -> None:
    days = [
        DailyBar(date(2026, 1, 5), 100, 102, 99, 101, 10),
        DailyBar(date(2026, 1, 12), 102, 104, 101, 103, 12),
    ]
    weeks = weekly_bars(days, week_end_seen=None)
    assert final_weeks(weeks) == [weeks[0]]
