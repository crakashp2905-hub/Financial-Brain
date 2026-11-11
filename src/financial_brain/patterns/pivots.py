"""Causal swing pivots (CP-0056, CP-0057, CP-0138).

A pivot at index ``i`` is a strict local maximum (for highs) or minimum (for
lows) over ``left`` bars before it and ``right`` bars after it. It becomes known
only once bar ``i + right`` has arrived, so ``known_at_index == i + right``.

Tie rule (deterministic, CP-0057): against the left window the extreme must be
strictly better; against the right window it must be at least as good. On a flat
top the *first* bar of the plateau is the pivot, and the result never depends on
bars after the confirmation window.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

PivotKind = Literal["high", "low"]


@dataclass(frozen=True)
class Pivot:
    index: int
    kind: PivotKind
    price: float
    known_at_index: int


def _check_series(values: Sequence[float], label: str) -> None:
    for pos, value in enumerate(values):
        if not isinstance(value, int | float) or isinstance(value, bool) or not math.isfinite(value):
            raise ValueError(f"{label}[{pos}] is not a finite number: {value!r}")


def causal_pivots(
    highs: Sequence[float], lows: Sequence[float], *, left: int = 2, right: int = 2
) -> list[Pivot]:
    """Return every pivot whose confirmation window fits inside the given bars."""
    if left < 1 or right < 1:
        raise ValueError("left and right windows must be >= 1")
    if len(highs) != len(lows):
        raise ValueError("highs and lows must have equal length")
    _check_series(highs, "highs")
    _check_series(lows, "lows")
    n = len(highs)
    out: list[Pivot] = []
    for i in range(left, n - right):
        is_high, is_low = window_pivot(highs[i - left : i + right + 1], lows[i - left : i + right + 1], left, right)
        if is_high:
            out.append(Pivot(i, "high", float(highs[i]), i + right))
        if is_low:
            out.append(Pivot(i, "low", float(lows[i]), i + right))
    return out


def window_pivot(
    highs: Sequence[float], lows: Sequence[float], left: int, right: int
) -> tuple[bool, bool]:
    """Classify the centre bar (position ``left``) of a window of ``left+right+1`` bars.

    Shared by the batch function and :class:`financial_brain.patterns.streaming.StreamingPivots`
    so both apply the identical rule.
    """
    if len(highs) != left + right + 1 or len(lows) != left + right + 1:
        raise ValueError("window must contain exactly left + right + 1 bars")
    lo_window = range(0, left)
    hi_window = range(left + 1, left + right + 1)
    is_high = all(highs[left] > highs[j] for j in lo_window) and all(
        highs[left] >= highs[j] for j in hi_window
    )
    is_low = all(lows[left] < lows[j] for j in lo_window) and all(
        lows[left] <= lows[j] for j in hi_window
    )
    return is_high, is_low


def known_pivots(pivots: Sequence[Pivot], as_of_index: int) -> list[Pivot]:
    """Filter to pivots already observable at ``as_of_index`` (inclusive)."""
    return [p for p in pivots if p.known_at_index <= as_of_index]
