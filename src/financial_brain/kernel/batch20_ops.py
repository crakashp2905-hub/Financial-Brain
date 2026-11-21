"""Operations, sweeps and lineage (FB-0420, FB-0422, FB-0425, FB-0427, FB-0431, FB-0433).


* ``time_block`` runs a callable and returns its result and elapsed seconds (profiling).
* ``sweep`` expands a parameter grid and refuses grids larger than the budget.
* ``validate_scenario`` checks a named regime scenario: shocks in [-1, 1] and unique names across a set.
* ``equal_weight_series`` averages strategy return series with equal weights and checks alignment.
* ``JobQueue`` runs at most ``max_running`` jobs at once and keeps FIFO order for queued work.
* ``ancestors`` walks a lineage graph and refuses cycles.
"""
from __future__ import annotations

import itertools
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from typing import TypeVar

T = TypeVar("T")


def time_block(fn: Callable[[], T]) -> tuple[T, float]:
    start = time.perf_counter()
    result = fn()
    return result, time.perf_counter() - start


def sweep(grid: Mapping[str, Sequence[object]], budget: int) -> list[dict[str, object]]:
    keys = sorted(grid)
    size = 1
    for k in keys:
        size *= len(grid[k])
    if size > budget:
        raise ValueError(f"grid of {size} exceeds budget {budget}")
    return [dict(zip(keys, combo, strict=True)) for combo in itertools.product(*(grid[k] for k in keys))]


def validate_scenario(name: str, shocks: Mapping[str, float], existing: set[str]) -> None:
    if not name:
        raise ValueError("scenario needs a name")
    if name in existing:
        raise ValueError(f"scenario {name} already exists")
    for asset, shock in shocks.items():
        if not (-1.0 <= shock <= 1.0):
            raise ValueError(f"shock for {asset} must be in [-1, 1]")


def equal_weight_series(series: Mapping[str, Sequence[float]]) -> list[float]:
    lengths = {len(v) for v in series.values()}
    if len(lengths) != 1:
        raise ValueError("series must have equal length")
    n = len(series)
    if n == 0:
        raise ValueError("no series")
    return [sum(v[i] for v in series.values()) / n for i in range(lengths.pop())]


class JobQueue:
    def __init__(self, max_running: int) -> None:
        if max_running < 1:
            raise ValueError("max_running must be positive")
        self.max_running = max_running
        self.queued: deque[str] = deque()
        self.running: list[str] = []

    def submit(self, job: str) -> None:
        self.queued.append(job)

    def start_next(self) -> str | None:
        if len(self.running) >= self.max_running or not self.queued:
            return None
        job = self.queued.popleft()
        self.running.append(job)
        return job

    def finish(self, job: str) -> None:
        self.running.remove(job)


def ancestors(parents: Mapping[str, Sequence[str]], node: str) -> set[str]:
    found: set[str] = set()
    path: set[str] = set()

    def visit(n: str) -> None:
        if n in path:
            raise ValueError(f"cycle through {n}")
        path.add(n)
        for p in parents.get(n, ()):
            if p not in found:
                found.add(p)
                visit(p)
        path.discard(n)

    visit(node)
    return found
