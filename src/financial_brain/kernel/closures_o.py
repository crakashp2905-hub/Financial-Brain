"""Closing two partial items (FB-0139, FB-0185).

* ``seeded_random_policy`` is a stochastic policy driven by an injected seed. The same seed and steps always produce
  the same actions, so a stochastic run can be reproduced.
* ``MarkSnapshot`` keeps the mark (mid of bid and ask) separate from the last trade price. The mark uses the mid when a
  two-sided quote exists and falls back to last only when it does not.
"""
from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal


def seeded_random_policy(seed: int, actions: Sequence[str], steps: int) -> list[str]:
    if not actions:
        raise ValueError("no actions to choose from")
    if steps < 0:
        raise ValueError("steps must be non-negative")
    rng = random.Random(seed)
    return [actions[rng.randrange(len(actions))] for _ in range(steps)]


@dataclass(frozen=True)
class MarkSnapshot:
    bid: Decimal | None
    ask: Decimal | None
    last: Decimal | None

    def mark(self) -> Decimal | None:
        if self.bid is not None and self.ask is not None and self.bid > 0 and self.ask > 0:
            if self.bid > self.ask:
                raise ValueError("crossed quote cannot be marked")
            return (self.bid + self.ask) / 2
        return self.last
