"""Geometric primitives on completed bars (CP-0058, CP-0060, CP-0061, CP-0066, CP-0067, CP-0073).

Rules enforced here:
- Tolerances are explicit policies (ATR multiple, percent, or ticks). A policy
  never mixes units; a detector must name one (CP-0058).
- Only bars with index <= ``known_upto`` may contribute to a level (CP-0060).
- Degenerate lines (vertical or identical points) are rejected, not approximated (CP-0061).
- Fractions are returned as ``None`` when out of bounds, never clamped (CP-0066).
- When one bar's range spans both a stop and a target, the result is
  ``AMBIGUOUS`` and never resolves in the trader's favour (CP-0067).
- Every acceptance or rejection can be recorded in a :class:`DecisionTrace` (CP-0073).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal


def _finite(label: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise ValueError(f"{label} must be a finite number, got {value!r}")
    return float(value)


@dataclass(frozen=True)
class TolerancePolicy:
    kind: Literal["atr", "pct", "tick"]
    amount: float

    def __post_init__(self) -> None:
        if self.kind not in ("atr", "pct", "tick"):
            raise ValueError(f"unknown tolerance kind {self.kind!r}")
        if _finite("amount", self.amount) <= 0:
            raise ValueError("tolerance amount must be positive")

    def value(self, *, price: float, atr: float | None = None, tick_size: float | None = None) -> float:
        """Absolute price tolerance. Missing inputs for the chosen unit raise, never default."""
        if self.kind == "atr":
            if atr is None or _finite("atr", atr) <= 0:
                raise ValueError("atr tolerance needs a positive atr")
            return self.amount * atr
        if self.kind == "pct":
            return self.amount * _finite("price", price) / 100.0
        if tick_size is None or _finite("tick_size", tick_size) <= 0:
            raise ValueError("tick tolerance needs a positive tick_size")
        return self.amount * tick_size


def horizontal_touches(
    highs: list[float], lows: list[float], level: float, tol: float, *, known_upto: int
) -> list[int]:
    """Indices (<= known_upto) of bars whose range overlaps the level band (CP-0060)."""
    if len(highs) != len(lows):
        raise ValueError("highs and lows must have equal length")
    _finite("level", level)
    _finite("tol", tol)
    last = min(known_upto, len(highs) - 1)
    return [
        i
        for i in range(0, last + 1)
        if lows[i] <= level + tol and highs[i] >= level - tol
    ]


@dataclass(frozen=True)
class Line:
    """Line in bar-index space: price = slope * index + intercept."""

    slope: float
    intercept: float

    @staticmethod
    def through(x1: int, y1: float, x2: int, y2: float) -> Line:
        if x1 == x2:
            raise ValueError("vertical or degenerate line rejected")
        slope = (_finite("y2", y2) - _finite("y1", y1)) / (x2 - x1)
        return Line(slope, _finite("y1", y1) - slope * x1)

    def at(self, x: float) -> float:
        return self.slope * x + self.intercept


def intersection_index(a: Line, b: Line, *, min_slope_gap: float = 1e-12) -> float | None:
    """Bar index where two lines cross, or ``None`` for parallel or near-parallel lines (CP-0061)."""
    gap = a.slope - b.slope
    if abs(gap) <= min_slope_gap:
        return None
    return (b.intercept - a.intercept) / gap


def fraction(numerator: float, denominator: float, *, lo: float = 0.0, hi: float = 1.0) -> float | None:
    """Ratio that is returned only when it lies in ``[lo, hi]``; otherwise ``None`` (CP-0066)."""
    _finite("numerator", numerator)
    _finite("denominator", denominator)
    if denominator == 0:
        return None
    value = numerator / denominator
    if not (lo <= value <= hi):
        return None
    return value


class BarOutcome(str, Enum):
    NEITHER = "NEITHER"
    STOP_ONLY = "STOP_ONLY"
    TARGET_ONLY = "TARGET_ONLY"
    AMBIGUOUS = "AMBIGUOUS"


def resolve_bar_ambiguity(
    *, high: float, low: float, stop: float, target: float, side: Literal["long", "short"]
) -> BarOutcome:
    """Whether a bar hits the stop, the target, both (ambiguous) or neither (CP-0067).

    For a long, the stop is below and the target above; the reverse for a short.
    Both levels inside one bar's range is ``AMBIGUOUS``: the intrabar order is
    unknown, so no favourable order is assumed.
    """
    _finite("high", high)
    _finite("low", low)
    if high < low:
        raise ValueError("high must be >= low")
    if side == "long":
        if not stop < target:
            raise ValueError("long requires stop < target")
        hit_stop = low <= stop
        hit_target = high >= target
    elif side == "short":
        if not target < stop:
            raise ValueError("short requires target < stop")
        hit_stop = high >= stop
        hit_target = low <= target
    else:
        raise ValueError(f"unknown side {side!r}")
    if hit_stop and hit_target:
        return BarOutcome.AMBIGUOUS
    if hit_stop:
        return BarOutcome.STOP_ONLY
    if hit_target:
        return BarOutcome.TARGET_ONLY
    return BarOutcome.NEITHER


@dataclass
class DecisionTrace:
    """Ordered record of each threshold check and whether it passed (CP-0073)."""

    checks: list[dict[str, Any]] = field(default_factory=list)

    def check(self, name: str, passed: bool, value: Any = None, threshold: Any = None) -> bool:
        self.checks.append({"name": name, "passed": bool(passed), "value": value, "threshold": threshold})
        return bool(passed)

    @property
    def accepted(self) -> bool:
        return bool(self.checks) and all(c["passed"] for c in self.checks)

    def to_dict(self) -> dict[str, Any]:
        return {"accepted": self.accepted, "checks": list(self.checks)}
