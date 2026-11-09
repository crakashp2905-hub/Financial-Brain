"""Causal swing pivots (FB-0107) and pattern lifecycle (FB-0113).

* ``confirmed_pivots`` marks a high or low as a pivot only once ``k`` later bars have been seen. Using only bars up to
  an index, a pivot confirmed by that index never changes if later data is truncated away.
* ``PatternLifecycle`` moves a pattern through CANDIDATE -> CONFIRMED | FAILED | EXPIRED. Terminal states cannot move,
  and replaying the same events always yields the same final state.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum


@dataclass(frozen=True)
class Pivot:
    index: int  # bar where the extreme occurred
    confirmed_at: int  # first bar whose data confirms it
    kind: str  # HIGH | LOW


def confirmed_pivots(highs: Sequence[float], lows: Sequence[float], k: int, upto: int) -> list[Pivot]:
    """Pivots confirmed using only bars 0..upto inclusive. Bar i is a pivot high if it is the strict maximum over
    i-k..i+k; it is confirmed at bar i+k, so it is only returned when i+k <= upto."""
    if k < 1:
        raise ValueError("k must be positive")
    pivots: list[Pivot] = []
    for i in range(k, upto - k + 1):
        if i + k > upto:
            break
        window_h = highs[i - k : i + k + 1]
        window_l = lows[i - k : i + k + 1]
        if highs[i] == max(window_h) and window_h.count(highs[i]) == 1:
            pivots.append(Pivot(i, i + k, "HIGH"))
        if lows[i] == min(window_l) and window_l.count(lows[i]) == 1:
            pivots.append(Pivot(i, i + k, "LOW"))
    return pivots


class PatternState(Enum):
    CANDIDATE = "CANDIDATE"
    CONFIRMED = "CONFIRMED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"


_TERMINAL = {PatternState.CONFIRMED, PatternState.FAILED, PatternState.EXPIRED}
_ALLOWED = {
    PatternState.CANDIDATE: {PatternState.CONFIRMED, PatternState.FAILED, PatternState.EXPIRED},
}


class IllegalTransition(Exception):
    pass


@dataclass
class PatternLifecycle:
    state: PatternState = PatternState.CANDIDATE
    history: list[PatternState] = field(default_factory=list)

    def move(self, target: PatternState) -> None:
        if self.state in _TERMINAL:
            raise IllegalTransition(f"{self.state.value} is terminal")
        if target not in _ALLOWED[self.state]:
            raise IllegalTransition(f"{self.state.value} -> {target.value} not allowed")
        self.history.append(self.state)
        self.state = target


def replay(events: Sequence[PatternState]) -> PatternState:
    """Deterministic replay: the same event sequence always yields the same final state, and any event after a
    terminal state is refused."""
    life = PatternLifecycle()
    for event in events:
        life.move(event)
    return life.state
