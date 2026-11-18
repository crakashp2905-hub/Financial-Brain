"""SMMA bands and prior-day Fibonacci levels (FB-2509 to FB-2511).

SMMA is Wilder smoothing (Pine ``ta.rma``): seeded with the simple mean of the first ``n`` values,
then ``s = (s * (n - 1) + x) / n``. Before ``n`` values exist the result is None (warm-up). A
missing bar (None) resets the series: no value is carried across a gap.
"""
from __future__ import annotations

from dataclasses import dataclass

FIB_RATIOS: tuple[float, ...] = (
    -323.4, -200.0, -161.8, -141.4, -100.0, -76.4, -61.8, -41.4, -27.0, -12.8, 0.0, 11.4, 23.6,
    38.2, 50.0, 61.8, 76.4, 88.6, 100.0, 112.8, 127.0, 141.4, 161.8, 176.4, 200.0, 261.8, 423.6)


def smma(values: list[float | None], n: int) -> list[float | None]:
    if n < 1:
        raise ValueError("period must be positive")
    out: list[float | None] = []
    seed: list[float] = []
    s: float | None = None
    for v in values:
        if v is None:
            seed, s = [], None
            out.append(None)
            continue
        if s is None:
            seed.append(v)
            if len(seed) == n:
                s = sum(seed) / n
            out.append(s)
        else:
            s = (s * (n - 1) + v) / n
            out.append(s)
    return out


@dataclass(frozen=True)
class Cluster20:
    low: float
    mid: float
    high: float


def cluster20(lows: list[float | None], closes: list[float | None], highs: list[float | None]
              ) -> list[Cluster20 | None]:
    lo, mi, hi = smma(lows, 20), smma(closes, 20), smma(highs, 20)
    return [Cluster20(a, b, c) if None not in (a, b, c) else None  # type: ignore[arg-type]
            for a, b, c in zip(lo, mi, hi, strict=True)]


def fib_levels(prev_day_low: float, prev_day_high: float, anchor: str) -> dict[float, float]:
    """Levels from the *previous completed* day only. ``anchor`` is an owner-chosen variant."""
    if prev_day_high < prev_day_low:
        raise ValueError("high below low")
    rng = prev_day_high - prev_day_low
    if anchor == "PREV_DAY_LOW_TO_HIGH":
        return {r: prev_day_low + rng * r / 100 for r in FIB_RATIOS}
    if anchor == "PREV_DAY_HIGH_TO_LOW":
        return {r: prev_day_high - rng * r / 100 for r in FIB_RATIOS}
    raise ValueError(f"unknown Fibonacci anchor {anchor!r}")
