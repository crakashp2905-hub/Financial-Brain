"""Geometry primitive tests (CP-0058, CP-0060, CP-0061, CP-0066, CP-0067, CP-0073)."""

from __future__ import annotations

import pytest

from financial_brain.patterns.geometry import (
    BarOutcome,
    DecisionTrace,
    Line,
    TolerancePolicy,
    fraction,
    horizontal_touches,
    intersection_index,
    resolve_bar_ambiguity,
)


def test_tolerance_units_are_explicit_and_never_default() -> None:
    assert TolerancePolicy("atr", 0.5).value(price=100, atr=4.0) == 2.0
    assert TolerancePolicy("pct", 1.0).value(price=200) == 2.0
    assert TolerancePolicy("tick", 3).value(price=100, tick_size=0.05) == pytest.approx(0.15)
    with pytest.raises(ValueError):
        TolerancePolicy("atr", 0.5).value(price=100)  # atr missing: no silent fallback
    with pytest.raises(ValueError):
        TolerancePolicy("tick", 3).value(price=100)
    with pytest.raises(ValueError):
        TolerancePolicy("pct", 0)


def test_horizontal_touches_ignore_bars_after_known_upto() -> None:
    highs = [10, 12, 10.5, 15, 10.2]
    lows = [9, 10, 9.5, 14, 9.9]
    touches = horizontal_touches(highs, lows, level=10.2, tol=0.2, known_upto=2)
    assert touches == [0, 1, 2]  # bar 4 is in the future and is excluded


def test_degenerate_and_parallel_lines_fail_cleanly() -> None:
    with pytest.raises(ValueError):
        Line.through(3, 1.0, 3, 2.0)
    a = Line.through(0, 0.0, 10, 10.0)
    b = Line.through(0, 5.0, 10, 15.0)
    assert intersection_index(a, b) is None
    c = Line.through(0, 10.0, 10, 0.0)
    assert intersection_index(a, c) == pytest.approx(5.0)


def test_fraction_is_bounded_or_none() -> None:
    assert fraction(3, 10) == 0.3
    assert fraction(15, 10) is None  # out of [0,1]
    assert fraction(1, 0) is None
    with pytest.raises(ValueError):
        fraction(float("nan"), 1)


def test_bar_spanning_stop_and_target_is_ambiguous_never_optimistic() -> None:
    outcome = resolve_bar_ambiguity(high=110, low=90, stop=95, target=105, side="long")
    assert outcome is BarOutcome.AMBIGUOUS
    assert resolve_bar_ambiguity(high=104, low=96, stop=95, target=105, side="long") is BarOutcome.NEITHER
    assert resolve_bar_ambiguity(high=106, low=99, stop=95, target=105, side="long") is BarOutcome.TARGET_ONLY
    assert resolve_bar_ambiguity(high=101, low=94, stop=95, target=105, side="long") is BarOutcome.STOP_ONLY


def test_short_side_uses_mirrored_levels() -> None:
    assert resolve_bar_ambiguity(high=106, low=94, stop=105, target=95, side="short") is BarOutcome.AMBIGUOUS
    with pytest.raises(ValueError):
        resolve_bar_ambiguity(high=106, low=94, stop=95, target=105, side="short")


def test_decision_trace_records_each_check_and_accepts_only_when_all_pass() -> None:
    trace = DecisionTrace()
    trace.check("geometry_fit", True, value=0.8, threshold=0.5)
    trace.check("liquidity", False, value=10, threshold=100)
    assert trace.accepted is False
    assert trace.to_dict()["checks"][1] == {"name": "liquidity", "passed": False, "value": 10, "threshold": 100}
    assert DecisionTrace().accepted is False  # empty trace never accepts
