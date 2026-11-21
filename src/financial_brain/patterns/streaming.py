"""Incremental pivot detection, one completed bar at a time (CP-0070, CP-0071).

The stream keeps only the last ``left + right + 1`` bars, so memory is constant
regardless of history length. It emits a pivot on the bar that completes its
right-hand window, which is exactly the bar ``Pivot.known_at_index`` names.
Output equals the batch :func:`causal_pivots` result on every prefix.
"""

from __future__ import annotations

import math
from collections import deque

from financial_brain.patterns.pivots import Pivot, window_pivot


class StreamingPivots:
    def __init__(self, *, left: int = 2, right: int = 2) -> None:
        if left < 1 or right < 1:
            raise ValueError("left and right windows must be >= 1")
        self.left = left
        self.right = right
        self._width = left + right + 1
        self._highs: deque[float] = deque(maxlen=self._width)
        self._lows: deque[float] = deque(maxlen=self._width)
        self._count = 0  # bars consumed so far; the newest bar has index count - 1

    def push(self, high: float, low: float) -> list[Pivot]:
        """Consume one completed bar and return pivots that became known on it."""
        for name, value in (("high", high), ("low", low)):
            if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
                raise ValueError(f"{name} must be a finite number, got {value!r}")
        self._highs.append(float(high))
        self._lows.append(float(low))
        self._count += 1
        if len(self._highs) < self._width:
            return []
        centre = self._count - 1 - self.right
        is_high, is_low = window_pivot(list(self._highs), list(self._lows), self.left, self.right)
        out: list[Pivot] = []
        if is_high:
            out.append(Pivot(centre, "high", self._highs[self.left], self._count - 1))
        if is_low:
            out.append(Pivot(centre, "low", self._lows[self.left], self._count - 1))
        return out

    @property
    def buffered_bars(self) -> int:
        """Bars currently held in memory; bounded by ``left + right + 1``."""
        return len(self._highs)
