"""Small closures of partial items (FB-0102, FB-0105, FB-0127, FB-0130, FB-0162, FB-0171, FB-0180, FB-0189, FB-0197).

* ``bollinger`` gives mid, upper and lower bands (SMA plus or minus k population standard deviations). Warm-up is None.
* ``combine_signals`` combines two aligned signal lists with AND or OR.
* ``rule_report`` summarises per-instrument rule checks into pass and fail groups, for the pre-paper report.
* ``dispatch_timers`` runs a callback for each scheduled time in ascending order and skips times with no callback.
* ``benchmark_granularities`` counts the events each replay granularity keeps.
* ``is_expired`` applies time-in-force expiry: DAY ends at the close of the placed day, GTC never expires, GTD
  expires after its expiry day.
* ``stamp_duty`` is a simple proportional duty on notional. The rate is an input, not a tax table.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from decimal import Decimal
from statistics import mean, pstdev
from typing import Any

from financial_brain.kernel.batch20b_market import replay_granularity


def bollinger(values: Sequence[float], window: int, k: float) -> list[tuple[float, float, float] | None]:
    if window < 2 or k <= 0:
        raise ValueError("window must be at least 2 and k positive")
    out: list[tuple[float, float, float] | None] = []
    for i in range(len(values)):
        if i + 1 < window:
            out.append(None)
            continue
        chunk = values[i + 1 - window : i + 1]
        mid = mean(chunk)
        sd = pstdev(chunk)
        out.append((mid, mid + k * sd, mid - k * sd))
    return out


def combine_signals(a: Sequence[int], b: Sequence[int], mode: str) -> list[int]:
    if len(a) != len(b):
        raise ValueError("signals must be aligned")
    if mode == "AND":
        return [1 if (x and y) else 0 for x, y in zip(a, b, strict=True)]
    if mode == "OR":
        return [1 if (x or y) else 0 for x, y in zip(a, b, strict=True)]
    raise ValueError("mode must be AND or OR")


def rule_report(results: Mapping[str, Sequence[str]]) -> dict[str, Any]:
    passed = sorted(name for name, reasons in results.items() if not reasons)
    failed = {name: list(reasons) for name, reasons in sorted(results.items()) if reasons}
    return {"pass": passed, "fail": failed}


def dispatch_timers(times: Sequence[int], callbacks: Mapping[int, Callable[[], Any]]) -> list[Any]:
    return [callbacks[t]() for t in sorted(times) if t in callbacks]


def benchmark_granularities(
    events: Sequence[tuple[int, str]], granularities: Sequence[str]
) -> dict[str, int]:
    return {g: len(replay_granularity(events, g)) for g in granularities}


def is_expired(tif: str, placed_day: int, today: int, expiry_day: int | None = None) -> bool:
    if tif == "DAY":
        return today > placed_day
    if tif == "GTC":
        return False
    if tif == "GTD":
        if expiry_day is None:
            raise ValueError("GTD needs an expiry day")
        return today > expiry_day
    raise ValueError(f"unknown time in force {tif!r}")


def stamp_duty(notional: Decimal, rate: Decimal) -> Decimal:
    if rate < 0:
        raise ValueError("rate must be non-negative")
    return notional * rate
