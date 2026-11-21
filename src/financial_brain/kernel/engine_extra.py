"""Engine extras: schema migration (FB-0175), simulated latency and stress (FB-0176, FB-0202), account isolation
(FB-0178) and seeded pessimistic fills (FB-0182).

* ``read_event`` accepts schema v1 (``px``) and v2 (``price``) and returns the v2 shape. Unknown versions are refused.
* ``LatencyModel`` draws seeded delays. In OUTAGE mode nothing is delivered. ``stress_run`` reports delivered counts for
  NORMAL, DEGRADED and OUTAGE modes.
* ``AccountScopedStore`` keeps each account's strategy state apart. A lookup with another account raises.
* ``pessimistic_fill`` draws a seeded price on the adverse half of the bar: BUY in [mid, high], SELL in [low, mid].
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


def read_event(record: dict[str, Any]) -> dict[str, Any]:
    version = record.get("schema_version")
    if version == 1:
        if "px" not in record:
            raise ValueError("v1 event missing 'px'")
        return {"schema_version": 2, "instrument": record["instrument"], "price": record["px"], "time": record["time"]}
    if version == 2:
        if "price" not in record:
            raise ValueError("v2 event missing 'price'")
        return dict(record)
    raise ValueError(f"unknown event schema version {version!r}")


class LinkMode(Enum):
    NORMAL = "NORMAL"
    DEGRADED = "DEGRADED"
    OUTAGE = "OUTAGE"


@dataclass
class LatencyModel:
    base_ms: float
    jitter_ms: float
    loss_rate: float  # used only in DEGRADED mode
    seed: int
    _rng: random.Random = field(init=False)

    def __post_init__(self) -> None:
        if not (0 <= self.loss_rate <= 1):
            raise ValueError("loss_rate must be between 0 and 1")
        self._rng = random.Random(self.seed)

    def deliver(self, mode: LinkMode) -> float | None:
        """Delay in ms, or None when the event is not delivered."""
        if mode is LinkMode.OUTAGE:
            return None
        if mode is LinkMode.DEGRADED and self._rng.random() < self.loss_rate:
            return None
        jitter = self._rng.uniform(0, self.jitter_ms)
        scale = 3.0 if mode is LinkMode.DEGRADED else 1.0
        return (self.base_ms + jitter) * scale


def stress_run(events: int, base_ms: float, jitter_ms: float, loss_rate: float, seed: int) -> dict[str, int]:
    out: dict[str, int] = {}
    for mode in LinkMode:
        model = LatencyModel(base_ms, jitter_ms, loss_rate, seed)
        delivered = sum(1 for _ in range(events) if model.deliver(mode) is not None)
        out[mode.value] = delivered
    return out


class IsolationError(PermissionError):
    pass


@dataclass
class AccountScopedStore:
    _state: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)

    def put(self, account: str, strategy: str, value: dict[str, Any]) -> None:
        self._state[(account, strategy)] = value

    def get(self, account: str, strategy: str) -> dict[str, Any]:
        key = (account, strategy)
        if key not in self._state:
            owners = {acct for acct, strat in self._state if strat == strategy}
            if owners and account not in owners:
                raise IsolationError(f"strategy {strategy} is not visible to account {account}")
            raise KeyError(key)
        return self._state[key]


def pessimistic_fill(side: str, low: float, high: float, seed: int) -> float:
    """Seeded fill on the adverse half of the bar. BUY pays in [mid, high]; SELL receives in [low, mid]."""
    if high < low:
        raise ValueError("high below low")
    mid = (low + high) / 2
    rng = random.Random(seed)
    if side == "BUY":
        return rng.uniform(mid, high)
    if side == "SELL":
        return rng.uniform(low, mid)
    raise ValueError("side must be BUY or SELL")
