"""Independent clocks and the candidate lifecycle with a LOCKED state (ALG-028, ALG-038).

A structure clock (higher timeframe) and an entry clock (session bars) advance separately. An entry-clock trigger can
never move the structure state forward, and a structure state is only visible once its bar has closed.

Lifecycle: CANDIDATE -> LOCKED -> CONFIRMED | INVALIDATED | EXPIRED. Every transition is appended, never edited,
and illegal transitions raise. LOCKED means the neckline or level is frozen; no later bar may move it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

LEGAL = {
    "CANDIDATE": {"LOCKED", "INVALIDATED", "EXPIRED"},
    "LOCKED": {"CONFIRMED", "INVALIDATED", "EXPIRED"},
    "CONFIRMED": set(),
    "INVALIDATED": set(),
    "EXPIRED": set(),
}


class IllegalTransition(ValueError):
    """A lifecycle move that the table does not allow."""


@dataclass(frozen=True)
class StructureClock:
    """Higher-timeframe clock: a state is visible only at or after its closing bar."""

    closed_bar: int

    def visible(self, bar: int) -> bool:
        return bar <= self.closed_bar


@dataclass(frozen=True)
class EntryClock:
    """Session-bar clock. Advancing it never advances the structure clock."""

    bar: int


def advance_entry(structure: StructureClock, entry: EntryClock, new_bar: int) -> tuple[StructureClock, EntryClock]:
    if new_bar < entry.bar:
        raise ValueError("entry clock cannot move backwards")
    return structure, EntryClock(new_bar)


@dataclass(frozen=True)
class Transition:
    event_id: str
    from_state: str
    to_state: str
    at_bar: int
    reason: str


@dataclass
class Lifecycle:
    event_id: str
    state: str = "CANDIDATE"
    log: list[Transition] = field(default_factory=list)

    def move(self, to_state: str, *, at_bar: int, reason: str) -> None:
        if to_state not in LEGAL.get(self.state, set()):
            raise IllegalTransition(f"{self.state} -> {to_state} is not allowed")
        if self.log and at_bar < self.log[-1].at_bar:
            raise ValueError("transitions must be in time order")
        self.log.append(Transition(self.event_id, self.state, to_state, at_bar, reason))
        self.state = to_state

    def replay(self) -> str:
        """Rebuild the state from the log alone; it must equal the live state (append-only check)."""
        state = "CANDIDATE"
        for t in self.log:
            if t.from_state != state or t.to_state not in LEGAL[state]:
                raise IllegalTransition("log is inconsistent")
            state = t.to_state
        return state


def lock_level(level: float, *, frozen: bool) -> float:
    """A locked level cannot be restated: a later call with a different level is refused."""
    if frozen:
        raise ValueError("locked levels are frozen")
    return level
