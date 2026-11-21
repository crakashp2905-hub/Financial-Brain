"""Engine checks and reference implementations (FB-0583, FB-0586, FB-0587, FB-0588, FB-0592, FB-0593, FB-0594,
FB-0595, FB-0600, FB-0601).

* ``migration_justified`` allows a Rust port only when profiling shows a hot path and a measured speedup clears a bar.
* ``encode_decimal`` and ``decode_decimal`` round-trip a Decimal exactly as text, keeping its precision.
* ``EventQueue`` pops by (time, insertion sequence), so equal-time events keep their arrival order.
* ``BoundedBuffer`` refuses an append beyond capacity rather than dropping data silently.
* ``abi_compatible`` accepts a library when its major version matches and its minor is not behind the host.
* ``matches_oracle`` compares a fast implementation with a Python reference over the same inputs.
* ``check_finite`` and ``multiply_bounded`` refuse NaN, infinity and overflow.
* ``benchmark`` returns operations per second for a callable.
* ``load_kernel`` falls back to the Python reference when the native build is absent, and says so.
* ``parity`` labels whether two engine outputs agree.
"""
from __future__ import annotations

import heapq
import math
import time
from collections import deque
from collections.abc import Callable, Sequence
from decimal import Decimal
from typing import Any


def migration_justified(hot_fraction: float, measured_speedup: float) -> bool:
    return hot_fraction >= 0.3 and measured_speedup >= 2.0


def encode_decimal(value: Decimal) -> str:
    return str(value)


def decode_decimal(text: str) -> Decimal:
    return Decimal(text)


class EventQueue:
    def __init__(self) -> None:
        self._heap: list[tuple[int, int, Any]] = []
        self._seq = 0

    def push(self, time_key: int, payload: Any) -> None:
        heapq.heappush(self._heap, (time_key, self._seq, payload))
        self._seq += 1

    def pop(self) -> Any:
        if not self._heap:
            raise IndexError("queue is empty")
        return heapq.heappop(self._heap)[2]


class BoundedBuffer:
    def __init__(self, capacity: int) -> None:
        if capacity < 1:
            raise ValueError("capacity must be positive")
        self.capacity = capacity
        self._items: deque[Any] = deque()

    def append(self, item: Any) -> None:
        if len(self._items) >= self.capacity:
            raise BufferError("buffer full; refusing to drop data")
        self._items.append(item)

    def __len__(self) -> int:
        return len(self._items)


def abi_compatible(library_version: str, host_version: str) -> bool:
    lib_major, lib_minor = (int(p) for p in library_version.split(".")[:2])
    host_major, host_minor = (int(p) for p in host_version.split(".")[:2])
    return lib_major == host_major and lib_minor >= host_minor


def matches_oracle(fast: Callable[[Any], Any], reference: Callable[[Any], Any], inputs: Sequence[Any]) -> bool:
    return all(fast(x) == reference(x) for x in inputs)


def check_finite(x: float) -> float:
    if math.isnan(x) or math.isinf(x):
        raise ValueError(f"non-finite value {x}")
    return x


def multiply_bounded(a: Decimal, b: Decimal, limit: Decimal) -> Decimal:
    product = a * b
    if abs(product) > limit:
        raise OverflowError(f"product {product} exceeds limit {limit}")
    return product


def benchmark(fn: Callable[[], Any], n: int) -> float:
    if n < 1:
        raise ValueError("n must be positive")
    start = time.perf_counter()
    for _ in range(n):
        fn()
    elapsed = time.perf_counter() - start
    return n / elapsed if elapsed > 0 else float("inf")


def load_kernel(native_available: bool) -> tuple[str, str]:
    if native_available:
        return "native", "NATIVE"
    return "python", "PYTHON_FALLBACK"


def parity(a: Any, b: Any) -> str:
    return "PARITY" if a == b else "MISMATCH"
