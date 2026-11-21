"""Strategy SDK: lifecycle, typed parameter schema and isolated runs (FB-0123..0125, FB-0130, FB-0137, FB-0139).

* A ``Strategy`` receives ``on_start``, ``on_event``, ``on_timer``, ``on_fill`` and ``on_stop``. It returns intents;
  it never writes fills, positions or ledgers.
* ``ParamSchema`` declares each parameter's type, default and range. Malformed or out-of-range parameters fail
  validation with an actionable message before any event is seen.
* ``run`` gives each strategy a fresh instance and a seeded RNG, so interleaved runs cannot share mutable state.
"""
from __future__ import annotations

import random
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol


class SchemaError(ValueError):
    pass


@dataclass(frozen=True)
class Param:
    name: str
    kind: type
    default: Any
    low: float | None = None
    high: float | None = None

    def check(self, value: Any) -> Any:
        v: Any = value
        if self.kind is int and isinstance(v, bool):
            raise SchemaError(f"{self.name}: booleans are not integers")
        if not isinstance(v, self.kind):
            raise SchemaError(f"{self.name}: expected {self.kind.__name__}, got {type(v).__name__}")
        number = float(value)
        if self.low is not None and number < self.low:
            raise SchemaError(f"{self.name}: {v} below minimum {self.low}")
        if self.high is not None and number > self.high:
            raise SchemaError(f"{self.name}: {v} above maximum {self.high}")
        return v


@dataclass(frozen=True)
class ParamSchema:
    params: tuple[Param, ...]

    def validate(self, given: Mapping[str, Any]) -> dict[str, Any]:
        known = {p.name for p in self.params}
        unknown = sorted(set(given) - known)
        if unknown:
            raise SchemaError(f"unknown parameters: {', '.join(unknown)}")
        out = {}
        for p in self.params:
            out[p.name] = p.check(given[p.name]) if p.name in given else p.default
        return out


@dataclass(frozen=True)
class Intent:
    instrument: str
    side: str
    quantity: int
    reason: str


class Strategy(Protocol):
    def on_start(self, params: dict[str, Any], rng: random.Random) -> None: ...
    def on_event(self, event: Any) -> list[Intent]: ...
    def on_timer(self, now: Any) -> list[Intent]: ...
    def on_fill(self, fill: Any) -> None: ...
    def on_stop(self) -> None: ...


@dataclass
class RunLog:
    intents: list[Intent] = field(default_factory=list)
    fills_seen: int = 0
    stopped: bool = False


def run(
    factory: Callable[[], Strategy],
    schema: ParamSchema,
    given: Mapping[str, Any],
    events: Sequence[Any],
    *,
    seed: int,
    fills: Sequence[Any] = (),
) -> RunLog:
    params = schema.validate(given)
    strategy = factory()
    rng = random.Random(seed)
    log = RunLog()
    strategy.on_start(params, rng)
    for ev in events:
        log.intents.extend(strategy.on_event(ev))
    for f in fills:
        strategy.on_fill(f)
        log.fills_seen += 1
    strategy.on_stop()
    log.stopped = True
    return log
