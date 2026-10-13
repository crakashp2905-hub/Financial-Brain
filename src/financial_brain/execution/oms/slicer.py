"""Split a parent order into child orders over the session: TWAP or VWAP, under a participation cap.

    TWAP   equal child sizes at equal intervals between ``start`` and ``end``
    VWAP   child sizes proportional to the expected share of the day's volume in each interval (the intraday volume
           curve from past sessions - ``execution/algos.Day.curve`` is the same idea on minute bars)
    cap    no child may exceed ``participation`` x the interval's expected volume; what the cap removes is pushed to
           later intervals, and anything still left at the end is reported as ``unfilled`` rather than forced

A child order is a limit at the decision price moved by ``offset_bp`` toward the market (a buy a little above,
a sell a little below), re-priced by the engine when the next interval comes; a child is never a market order.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Child:
    at_minute: int            #: minutes after 09:15
    qty: int


def schedule(qty: int, *, kind: str = "VWAP", start: int = 15, end: int = 360, intervals: int = 12,
             curve: np.ndarray | None = None, expected_volume: float | None = None,
             participation: float = 0.10) -> dict:
    """``curve``: the expected share of the day's volume in each of ``intervals`` slices (sums to 1);
    ``expected_volume``: the day's expected shares traded (adv in shares). Both optional for TWAP."""
    if qty <= 0:
        return {"children": [], "unfilled": 0}
    edges = np.linspace(start, end, intervals + 1)
    if kind == "TWAP" or curve is None:
        w = np.full(intervals, 1.0 / intervals)
    else:
        w = np.asarray(curve, dtype=float)
        w = w / w.sum()
    target = w * qty
    cap = (participation * expected_volume * w) if expected_volume else np.full(intervals, np.inf)
    sizes, carry = np.zeros(intervals), 0.0
    for i in range(intervals):
        want = target[i] + carry
        sizes[i] = min(want, cap[i])
        carry = want - sizes[i]
    ints = np.floor(sizes).astype(int)
    short = qty - int(ints.sum()) - int(np.floor(carry))
    for i in np.argsort(-(sizes - ints)):            # hand the rounding remainder to the largest fractions, under cap
        if short <= 0:
            break
        if ints[i] + 1 <= cap[i]:
            ints[i] += 1
            short -= 1
    children = [Child(int(edges[i]), int(q)) for i, q in enumerate(ints) if q > 0]
    return {"children": children, "unfilled": int(qty - sum(c.qty for c in children))}


def child_limit(decision_px: float, side: str, offset_bp: float = 5.0) -> float:
    from .model import BUY
    from .risk_gate import round_to_tick
    px = decision_px * (1 + offset_bp / 1e4) if side == BUY else decision_px * (1 - offset_bp / 1e4)
    return round_to_tick(px, side)
