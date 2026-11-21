"""Wave 9 detector tests: pennant, channel, cup-with-handle, high-tight flag, triple extrema, rounding,
broadening, diamond, island reversal, three methods, failed gaps, throwback states, levels, complexity caps
and observational support/resistance (CP-0081..0116, CP-0104, CP-0105, CP-0106, CP-0115)."""

from __future__ import annotations

import math
import random
from collections.abc import Callable

import pytest

from financial_brain.patterns import detectors2 as d2
from financial_brain.patterns.detectors import Bars, Formation

Detector = Callable[[Bars], list[Formation]]
NEW_DETECTORS: tuple[Detector, ...] = (
    d2.pennant,
    d2.channels,
    d2.cup_handle,
    d2.high_tight_flag,
    d2.triple_extrema,
    d2.rounding,
    d2.broadening,
    d2.diamond,
    d2.island_reversal,
    d2.three_methods,
    d2.failed_gaps,
)


def mid_bars(closes: list[float], spread: float = 0.2) -> Bars:
    """Bars whose open is the midpoint of the previous and current close, so a trough bar's high stays below the prior peak."""
    opens = [closes[0]] + [(closes[i - 1] + closes[i]) / 2 for i in range(1, len(closes))]
    highs = [max(o, c) + spread for o, c in zip(opens, closes, strict=True)]
    lows = [min(o, c) - spread for o, c in zip(opens, closes, strict=True)]
    return Bars(tuple(opens), tuple(highs), tuple(lows), tuple(closes))


def explicit(rows: list[tuple[float, float, float, float]]) -> Bars:
    return Bars(
        tuple(r[0] for r in rows), tuple(r[1] for r in rows), tuple(r[2] for r in rows), tuple(r[3] for r in rows)
    )


def _walk(seed: int, n: int = 120) -> Bars:
    rng = random.Random(seed)
    closes = [100.0]
    for _ in range(n - 1):
        closes.append(max(5.0, closes[-1] * (1 + rng.gauss(0, 0.012))))
    return mid_bars(closes, spread=0.3)


def _signature(formations: list[Formation]) -> list[tuple[object, ...]]:
    return sorted(
        (f.detector, f.start_index, f.candidate_known_index, f.confirmed_index, f.failed_index, f.confirmed_direction)
        for f in formations
    )


def _assert_prefix_invariant(detector: Detector, bars: Bars) -> None:
    full = detector(bars)
    for t in range(30, len(bars), 3):
        seen = detector(bars.head(t + 1))
        expected = [g for f in full if (g := f.as_of(t)) is not None]
        assert _signature(seen) == _signature(expected), (detector.__name__, t)


# ---------------------------------------------------------------- positive examples
def _pennant_bars() -> Bars:
    closes = [100 + 1.5 * i for i in range(9)]
    for k in range(18):
        closes.append(112 + (2.0 - 0.1 * k) * (1 if k % 2 == 0 else -1))
    closes += [116, 118, 120]
    return mid_bars(closes)


def _cup_bars() -> Bars:
    pts = [(0, 90), (10, 100), (40, 84), (65, 99.6), (71, 96.5), (80, 101), (95, 104)]
    closes: list[float] = []
    for (x0, y0), (x1, y1) in zip(pts, pts[1:], strict=False):
        closes.extend(y0 + (y1 - y0) * (x - x0) / (x1 - x0) for x in range(x0, x1))
    closes.append(104.0)
    return mid_bars(closes, spread=0.3)


def _three_bars() -> Bars:
    return explicit(
        [
            (98, 99, 97.5, 98.5),
            (99, 100, 98.5, 99.5),
            (100, 106.2, 99.8, 106),
            (105.5, 105.9, 104.5, 104.8),
            (104.9, 105.5, 104.3, 104.6),
            (104.7, 105.2, 104.5, 104.6),
            (105, 106.5, 104.8, 106.5),
        ]
    )


def _island_bars() -> Bars:
    return explicit(
        [
            (99.5, 100.5, 99, 100),
            (100, 101, 99.5, 100.8),
            (100.8, 101.0, 100.2, 100.5),
            (103.6, 104.5, 103.5, 104.0),
            (104, 106, 103.8, 105.5),
            (105.4, 107, 104.5, 106.5),
            (102.5, 103, 101, 101.5),
            (101, 101.5, 99, 99.5),
            (99.5, 100, 98, 98.5),
        ]
    )


def test_pennant_positive_example_is_bullish_and_neutral() -> None:
    found = d2.pennant(_pennant_bars())
    assert found and all(f.detector == "pennant_bullish" for f in found)
    assert all(f.direction == "neutral" for f in found)
    assert all(f.geometry["window"] == 18 for f in found)  # bounded duration (CP-0081)


def test_cup_with_handle_positive_example_confirms_on_breakout() -> None:
    found = d2.cup_handle(_cup_bars())
    assert len(found) == 1
    cup = found[0]
    assert cup.detector == "cup_with_handle"
    assert cup.geometry["cup_low"] < cup.geometry["right_lip"] < cup.geometry["handle_end"]
    assert cup.confirmed_index is not None and cup.confirmed_direction == "bullish"  # CP-0084


def test_island_reversal_positive_example_is_top_with_two_gaps() -> None:
    found = d2.island_reversal(_island_bars())
    assert [f.detector for f in found] == ["island_reversal_top"]
    assert found[0].geometry["gap_in"] < found[0].geometry["gap_out"]


def test_three_methods_positive_example_confirms_only_after_continuation_close() -> None:
    found = d2.three_methods(_three_bars())
    assert [f.detector for f in found] == ["three_methods_bullish"]
    assert found[0].candidate_known_index == 5 and found[0].confirmed_index == 6  # CP-0113


def test_rounding_reports_each_bottom_once() -> None:
    bars = mid_bars([100 - 0.02 * (i - 30) ** 2 for i in range(80)], spread=0.3)
    found = d2.rounding(bars)
    assert [f.detector for f in found] == ["rounding_top"]  # downward parabola is a top
    assert len(found) == 1


def test_diamond_is_candidate_only_and_bounded_to_sixty_bars() -> None:
    bars = mid_bars([100 + (2 + 3 * min(i, 60 - i) / 30) * (1 if i % 2 == 0 else -1) for i in range(61)])
    found = d2.diamond(bars)
    assert found and all(f.detector == "diamond_candidate" and f.confirmed_index is None for f in found)  # CP-0101


def test_broadening_names_follow_trailing_move() -> None:
    bars = mid_bars([100 + (2 + 0.15 * k) * (1 if k % 2 == 0 else -1) for k in range(60)])
    names = {f.detector for f in d2.broadening(bars)}
    assert names <= {"broadening_top", "broadening_bottom"} and names


def test_high_tight_flag_requires_the_steep_rise_and_shallow_flag() -> None:
    closes = [50.0 * 1.025**i for i in range(40)]
    top = closes[-1]
    closes += [top - 0.2 * i for i in range(1, 13)]
    closes += [closes[-1] + 1.0 for _ in range(6)]
    found = d2.high_tight_flag(mid_bars(closes, spread=0.2))
    assert found and all(f.detector == "high_tight_flag" for f in found)
    assert all(f.geometry["retrace"] <= 0.25 for f in found)


def test_failed_gap_fill_is_hidden_until_it_has_happened() -> None:
    bars = explicit([(10, 10.5, 9.5, 10.2), (10.2, 10.5, 9.8, 10.2), (12, 12.8, 11.5, 12.4), (12.3, 12.6, 12.0, 12.5), (11.8, 12.0, 10.4, 10.9)])
    (gap,) = d2.failed_gaps(bars)
    assert gap.detector == "gap_up" and gap.failed_index == 4
    assert gap.as_of(3) is not None and gap.as_of(3).failed_index is None  # not yet filled at bar 3
    assert gap.as_of(4).failed_index == 4  # knowable once the fill bar has closed


# ---------------------------------------------------------------- causality for every wave-9 detector
@pytest.mark.parametrize("detector", NEW_DETECTORS, ids=lambda d: d.__name__)
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_every_wave9_detector_is_prefix_invariant(detector: Detector, seed: int) -> None:
    _assert_prefix_invariant(detector, _walk(seed))


@pytest.mark.parametrize(
    "detector,bars",
    [
        (d2.pennant, _pennant_bars()),
        (d2.cup_handle, _cup_bars()),
        (d2.island_reversal, _island_bars()),
        (d2.three_methods, _three_bars()),
    ],
    ids=["pennant", "cup", "island", "three_methods"],
)
def test_positive_examples_are_prefix_invariant(detector: Detector, bars: Bars) -> None:
    _assert_prefix_invariant(detector, bars)


def test_no_wave9_candidate_carries_a_direction_before_confirmation() -> None:
    for detector in NEW_DETECTORS:
        for f in detector(_walk(7)):
            if f.confirmed_index is None:
                assert f.direction == "neutral" and f.confirmed_direction is None


# ---------------------------------------------------------------- overlays, complexity and observational support
def test_levels_never_produce_a_profit_target() -> None:
    f = Formation("rectangle", "1", 0, 20, geometry={"top": 110.0, "bottom": 100.0})
    levels = d2.levels(f)
    assert levels == {
        "breakout_level": 110.0,
        "invalidation_level": 100.0,
        "profit_target": None,
        "target_policy": "not_modelled",
    }  # CP-0105


def test_complexity_cap_rejects_shapes_with_too_many_free_points() -> None:
    simple = Formation("triple_bottom", "1", 0, 20, geometry={"extrema": [1, 5, 9], "neckline": 105.0})
    flexible = Formation("diamond_candidate", "1", 0, 40, geometry={"pivots": list(range(8))})
    assert d2.within_complexity_cap(simple)
    assert not d2.within_complexity_cap(flexible)  # CP-0106


def test_throwback_state_is_causal_and_reports_held_or_failed() -> None:
    closes = [100.0] * 5 + [105.0, 106.0, 106.5, 104.0, 107.0, 108.0, 109.0]
    bars = mid_bars(closes, spread=0.2)
    f = Formation("rectangle", "1", 0, 4, confirmed_index=6, confirmed_direction="bullish")
    level = 104.0
    assert d2.throwback_state(bars, f, level, t=5) is None  # confirmation not yet visible
    state = d2.throwback_state(bars, f, level, t=len(closes) - 1)
    assert state in {"THROWBACK_HELD", "THROWBACK_FAILED", "PULLBACK_TOUCHED"}  # CP-0104
    assert d2.throwback_state(bars, f, level, t=len(closes) - 1) == state  # deterministic


def test_throwback_failed_when_close_loses_the_breakout_level() -> None:
    closes = [100.0] * 5 + [105.0, 106.0, 103.0, 102.0]
    bars = mid_bars(closes, spread=0.2)
    f = Formation("rectangle", "1", 0, 4, confirmed_index=6, confirmed_direction="bullish")
    assert d2.throwback_state(bars, f, 104.0, t=len(closes) - 1) == "THROWBACK_FAILED"


def test_sr_confluence_is_observational_with_no_multiplier() -> None:
    bars = mid_bars([100 + 2 * math.sin(i / 2) for i in range(60)], spread=0.3)
    out = d2.sr_confluence(bars, level=100.0, t=50)
    assert out["observational_only"] is True and out["multiplier"] is None  # CP-0115
    assert out["touches"] >= 0


def test_discover_extended_is_deterministic_and_sorted() -> None:
    bars = _walk(11)
    first = d2.discover_extended(bars)
    assert first == d2.discover_extended(bars)
    keys = [f.candidate_known_index for f in first]
    assert keys == sorted(keys)
