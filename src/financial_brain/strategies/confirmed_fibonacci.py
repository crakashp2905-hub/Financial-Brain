"""Fibonacci levels anchored only to pivots already observable at a query bar."""
from __future__ import annotations

from math import isfinite

from .four_node.nodes import Node

DEFAULT_RATIOS = (0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0, 1.272, 1.618)


def confirmed_fibonacci_levels(
    first: Node,
    second: Node,
    *,
    as_of_bar: int,
    ratios: tuple[float, ...] = DEFAULT_RATIOS,
) -> dict[float, float]:
    """Project ratios along two alternating, chronologically ordered confirmed pivots.

    ``as_of_bar`` must be at or after both pivots' observable timestamps. A returned
    level is therefore unavailable before the second pivot's right-side confirmation.
    This computes geometry only; it makes no claim that a level predicts price.
    """
    if as_of_bar < 0 or first.pivot_at >= second.pivot_at:
        raise ValueError("anchors must be chronological and as_of_bar nonnegative")
    if first.side == second.side:
        raise ValueError("anchors must alternate between confirmed high and low pivots")
    if max(int(first.observable_at or 0), int(second.observable_at or 0)) > as_of_bar:
        raise ValueError("an anchor was not observable at as_of_bar")
    if not isfinite(first.price) or not isfinite(second.price):
        raise ValueError("anchor prices must be finite")
    if not ratios or any(not isfinite(ratio) for ratio in ratios):
        raise ValueError("ratios must be a nonempty finite sequence")
    delta = second.price - first.price
    return {ratio: first.price + delta * ratio for ratio in ratios}
