"""Confirmed fractal nodes and the N1-N2-N3 zigzag (FB-2512 to FB-2516).

A pivot high at bar ``i`` is confirmed only after ``right`` further bars close, so ``confirmed_at``
is bar ``i + right`` and it is not observable before that bar's close. Emitted nodes never change.
Consecutive same-side pivots collapse to the more extreme one *only while it is still the latest
node* (the change is itself a new event, so earlier emissions are preserved in the event log).
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from hashlib import sha256
import re


@dataclass(frozen=True)
class Node:
    side: int            # +1 high, -1 low
    price: float
    pivot_at: int        # bar index of the pivot
    confirmed_at: int    # bar index whose close confirms it (observable_at)
    observable_at: int | None = None
    node_id: str = ""
    parent_chain: tuple[str, ...] = ()
    source_bar_ids: tuple[str, ...] = ()
    source_sha256: str | None = None

    def __post_init__(self) -> None:
        if self.side not in (-1, 1):
            raise ValueError("node side must be -1 (low) or +1 (high)")
        if self.pivot_at < 0 or self.confirmed_at < self.pivot_at:
            raise ValueError("invalid pivot/confirmation indices")
        observed = self.confirmed_at if self.observable_at is None else self.observable_at
        if observed < self.confirmed_at:
            raise ValueError("node cannot be observed before confirmation")
        object.__setattr__(self, "observable_at", observed)
        if self.source_sha256 is not None and not re.fullmatch(r"[0-9a-f]{64}", self.source_sha256):
            raise ValueError("node source must be a lowercase SHA-256")
        if not self.node_id:
            material = (f"{self.side}:{self.pivot_at}:{self.confirmed_at}:{self.price!r}:"
                        f"{','.join(self.source_bar_ids)}:{self.source_sha256 or ''}")
            object.__setattr__(self, "node_id", sha256(material.encode()).hexdigest()[:20])


def pivots(highs: list[float], lows: list[float], left: int, right: int, *,
           tie_policy: str = "STRICT", bar_ids: list[str] | None = None,
           source_sha256s: list[str] | None = None) -> list[Node]:
    """Confirmed pivots. Tie policy is STRICT, LEFTMOST or RIGHTMOST.

    A pivot is observable at the close of its right confirmation bar. ``bar_ids`` allows
    immutable raw-bar lineage to be retained; absent IDs receive deterministic index IDs.
    """
    if left < 1 or right < 1:
        raise ValueError("left/right confirmation windows must be positive")
    if tie_policy not in {"STRICT", "LEFTMOST", "RIGHTMOST"}:
        raise ValueError("tie_policy must be STRICT, LEFTMOST or RIGHTMOST")
    if len(highs) != len(lows):
        raise ValueError("high and low arrays must have equal length")
    if bar_ids is not None and len(bar_ids) != len(highs):
        raise ValueError("bar_ids must align with OHLC arrays")
    if source_sha256s is not None and (len(source_sha256s) != len(highs) or any(
            not re.fullmatch(r"[0-9a-f]{64}", value) for value in source_sha256s)):
        raise ValueError("bar source hashes must align and be lowercase SHA-256 values")
    out = []
    for i in range(left, len(highs) - right):
        h = highs[i]
        left_highs, right_highs = highs[i - left:i], highs[i + 1:i + right + 1]
        hi_left = all(h > v if tie_policy != "RIGHTMOST" else h >= v for v in left_highs)
        hi_right = all(h > v if tie_policy != "LEFTMOST" else h >= v for v in right_highs)
        ids = tuple(bar_ids[j] for j in range(i - left, i + right + 1)) if bar_ids else tuple(
            f"bar:{j}" for j in range(i - left, i + right + 1))
        source_hash = sha256("|".join(source_sha256s[j] for j in range(i - left, i + right + 1))
                             .encode()).hexdigest() if source_sha256s else None
        if hi_left and hi_right:
            out.append(Node(1, h, i, i + right, source_bar_ids=ids, source_sha256=source_hash))
        lo = lows[i]
        left_lows, right_lows = lows[i - left:i], lows[i + 1:i + right + 1]
        lo_left = all(lo < v if tie_policy != "RIGHTMOST" else lo <= v for v in left_lows)
        lo_right = all(lo < v if tie_policy != "LEFTMOST" else lo <= v for v in right_lows)
        if lo_left and lo_right:
            out.append(Node(-1, lo, i, i + right, source_bar_ids=ids, source_sha256=source_hash))
    return sorted(out, key=lambda n: (n.confirmed_at, n.pivot_at))


@dataclass
class Zigzag:
    nodes: list[Node] = field(default_factory=list)
    events: list[tuple[str, Node]] = field(default_factory=list)
    min_swing: float = 0.0

    def __post_init__(self) -> None:
        if self.min_swing < 0:
            raise ValueError("minimum swing must be nonnegative")

    def feed(self, n: Node) -> None:
        if self.nodes and self.nodes[-1].side == n.side:
            last = self.nodes[-1]
            more = n.price > last.price if n.side == 1 else n.price < last.price
            if more:
                updated = replace(n, parent_chain=last.parent_chain)
                self.nodes[-1] = updated
                self.events.append(("REPLACED_LATEST", updated))
            return
        if self.nodes and abs(n.price - self.nodes[-1].price) < self.min_swing:
            return
        ancestry = ((self.nodes[-1].node_id,) + self.nodes[-1].parent_chain[:7]
                    if self.nodes else ())
        updated = replace(n, parent_chain=ancestry)
        self.nodes.append(updated)
        self.events.append(("ADDED", updated))

    def last_three(self, at_bar: int) -> tuple[Node, Node, Node] | None:
        """N1, N2, N3 observable at ``at_bar`` (confirmed at or before it)."""
        known = [n for n in self.nodes if n.observable_at is not None
                 and n.observable_at <= at_bar]
        if len(known) < 3:
            return None
        n1, n2, n3 = known[-3], known[-2], known[-1]
        return n1, n2, n3


def build(highs: list[float], lows: list[float], left: int = 2, right: int = 2, *,
          tie_policy: str = "STRICT", min_swing: float = 0.0,
          bar_ids: list[str] | None = None) -> Zigzag:
    z = Zigzag(min_swing=min_swing)
    for n in pivots(highs, lows, left, right, tie_policy=tie_policy, bar_ids=bar_ids):
        z.feed(n)
    return z
