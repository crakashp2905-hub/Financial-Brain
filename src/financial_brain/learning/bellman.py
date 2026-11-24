"""Deterministic tabular Bellman operators for small offline-RL contract fixtures.

These operators are mathematical primitives only. They do not estimate market rewards,
support OPE, or authorize policy selection from Financial-Brain data.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping


class BellmanError(ValueError):
    """Invalid finite-state Bellman input."""


@dataclass(frozen=True, slots=True)
class Transition:
    state: str
    action: str
    reward: float
    next_state: str | None
    terminal: bool

    def __post_init__(self) -> None:
        if not self.state.strip() or not self.action.strip():
            raise BellmanError("state and action identities are required")
        if not math.isfinite(self.reward):
            raise BellmanError("reward must be finite")
        if self.terminal and self.next_state is not None:
            raise BellmanError("terminal transition cannot bootstrap from a next state")
        if not self.terminal and (self.next_state is None or not self.next_state.strip()):
            raise BellmanError("nonterminal transition requires next_state")


def target(transition: Transition, q_values: Mapping[tuple[str, str], float], *,
           gamma: float) -> float:
    """Return r for terminal rows, otherwise r + gamma * max_a Q(s',a)."""
    if not 0 <= gamma <= 1 or not math.isfinite(gamma):
        raise BellmanError("discount gamma must be finite and in [0, 1]")
    if transition.terminal:
        return transition.reward
    assert transition.next_state is not None
    next_values = [value for (state, _), value in q_values.items() if state == transition.next_state]
    if not next_values:
        raise BellmanError("nonterminal next state has no supported action values")
    if any(not math.isfinite(value) for value in next_values):
        raise BellmanError("Q-values must be finite")
    return transition.reward + gamma * max(next_values)


def fitted_q_iteration(rows: tuple[Transition, ...], *, gamma: float, iterations: int = 1) -> dict[tuple[str, str], float]:
    """Deterministic batch tabular FQI over logged (state, action) pairs only.

    Repeated state-action observations are averaged. No unseen actions are invented and
    no statistical or market-validity claim is made by this small reference operator.
    """
    if not rows or iterations < 1:
        raise BellmanError("non-empty transitions and positive iteration count are required")
    keys = sorted({(row.state, row.action) for row in rows})
    q: dict[tuple[str, str], float] = {key: 0.0 for key in keys}
    grouped: dict[tuple[str, str], list[Transition]] = {key: [] for key in keys}
    for row in rows:
        grouped[(row.state, row.action)].append(row)
    for _ in range(iterations):
        updated: dict[tuple[str, str], float] = {}
        for key in keys:
            values = [target(row, q, gamma=gamma) for row in grouped[key]]
            updated[key] = sum(values) / len(values)
        q = updated
    return q


__all__ = ["BellmanError", "Transition", "fitted_q_iteration", "target"]
