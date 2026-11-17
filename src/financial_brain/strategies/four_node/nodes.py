"""Confirmed fractal nodes and the N1-N2-N3 zigzag (FB-2512 to FB-2516).

A pivot high at bar ``i`` is confirmed only after ``right`` further bars close, so ``confirmed_at``
is bar ``i + right`` and it is not observable before that bar's close. Emitted nodes never change.
Consecutive same-side pivots collapse to the more extreme one *only while it is still the latest
node* (the change is itself a new event, so earlier emissions are preserved in the event log).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Node:
    side: int            # +1 high, -1 low
    price: float
    pivot_at: int        # bar index of the pivot
    confirmed_at: int    # bar index whose close confirms it (observable_at)


def pivots(highs: list[float], lows: list[float], left: int, right: int) -> list[Node]:
    """Williams-style pivots; ties count as not a pivot (strict on both sides)."""
    out = []
    for i in range(left, len(highs) - right):
        h = highs[i]
        if all(h > highs[j] for j in range(i - left, i)) and all(h > highs[j] for j in range(i + 1, i + right + 1)):
            out.append(Node(1, h, i, i + right))
        lo = lows[i]
        if all(lo < lows[j] for j in range(i - left, i)) and all(lo < lows[j] for j in range(i + 1, i + right + 1)):
            out.append(Node(-1, lo, i, i + right))
    return sorted(out, key=lambda n: (n.confirmed_at, n.pivot_at))


@dataclass
class Zigzag:
    nodes: list[Node] = field(default_factory=list)
    events: list[tuple[str, Node]] = field(default_factory=list)

    def feed(self, n: Node) -> None:
        if self.nodes and self.nodes[-1].side == n.side:
            last = self.nodes[-1]
            more = n.price > last.price if n.side == 1 else n.price < last.price
            if more:
                self.nodes[-1] = n
                self.events.append(("REPLACED_LATEST", n))
            return
        self.nodes.append(n)
        self.events.append(("ADDED", n))

    def last_three(self, at_bar: int) -> tuple[Node, Node, Node] | None:
        """N1, N2, N3 observable at ``at_bar`` (confirmed at or before it)."""
        known = [n for n in self.nodes if n.confirmed_at <= at_bar]
        if len(known) < 3:
            return None
        n1, n2, n3 = known[-3], known[-2], known[-1]
        return n1, n2, n3


def build(highs: list[float], lows: list[float], left: int = 2, right: int = 2) -> Zigzag:
    z = Zigzag()
    for n in pivots(highs, lows, left, right):
        z.feed(n)
    return z
