"""Market event handling (FB-0167, FB-0169, FB-0170, FB-0171, FB-0179, FB-0180, FB-0186, FB-0219).

* ``order_events`` sorts events per instrument by time, then by kind (quote, trade, bar, venue state), then sequence.
* ``route`` returns subscriber ids whose filters match an event. A filter field of None is a wildcard.
* ``run_stages`` runs independent stages on the same input. One stage failing does not stop the others.
* ``session_timers`` gives the minutes at which a session callback fires: the open, every step, and the close.
* ``sync_skew`` checks that cross-asset leg timestamps are within a tolerance of each other.
* ``replay_granularity`` keeps only the event kinds a granularity asks for (bar, quote or tick).
* ``book_apply`` applies incremental level updates to a book; a size of zero removes the level.
* ``normalise_broker`` maps vendor codes onto kernel event names. Unknown codes are refused.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

KIND_RANK = {"QUOTE": 0, "TRADE": 1, "BAR": 2, "VENUE_STATE": 3}
GRANULARITY_KINDS = {"TICK": {"QUOTE", "TRADE", "VENUE_STATE"}, "QUOTE": {"QUOTE"}, "BAR": {"BAR"}}
BROKER_CODES = {"A": "ACK", "R": "REJECT", "T": "TRADE", "F": "FILL", "P": "POSITION"}


def order_events(events: Sequence[tuple[str, int, int, str]]) -> list[tuple[str, int, int, str]]:
    for _, _, _, kind in events:
        if kind not in KIND_RANK:
            raise ValueError(f"unknown kind {kind!r}")
    return sorted(events, key=lambda e: (e[0], e[1], KIND_RANK[e[3]], e[2]))


def route(subscriptions: Mapping[str, Mapping[str, str | None]], event: Mapping[str, str]) -> list[str]:
    matched: list[str] = []
    for sub_id, filt in subscriptions.items():
        ok = all(v is None or event.get(k) == v for k, v in filt.items())
        if ok:
            matched.append(sub_id)
    return sorted(matched)


def run_stages(stages: Sequence[tuple[str, Callable[[Any], Any]]], payload: Any) -> dict[str, tuple[str, Any]]:
    out: dict[str, tuple[str, Any]] = {}
    for name, fn in stages:
        try:
            out[name] = ("ok", fn(payload))
        except Exception as exc:  # isolate each stage
            out[name] = ("error", f"{type(exc).__name__}: {exc}")
    return out


def session_timers(open_min: int, close_min: int, every: int) -> list[int]:
    if every <= 0 or close_min < open_min:
        raise ValueError("invalid session timer")
    times = list(range(open_min, close_min, every))
    if not times or times[-1] != close_min:
        times.append(close_min)
    return times


def sync_skew(leg_times_ms: Mapping[str, int], tolerance_ms: int) -> tuple[bool, int]:
    if not leg_times_ms:
        raise ValueError("no legs")
    skew = max(leg_times_ms.values()) - min(leg_times_ms.values())
    return skew <= tolerance_ms, skew


def replay_granularity(events: Sequence[tuple[int, str]], granularity: str) -> list[tuple[int, str]]:
    if granularity not in GRANULARITY_KINDS:
        raise ValueError(f"unknown granularity {granularity!r}")
    keep = GRANULARITY_KINDS[granularity]
    return sorted((e for e in events if e[1] in keep), key=lambda e: e[0])


def book_apply(book: Mapping[float, int], updates: Sequence[tuple[float, int]]) -> dict[float, int]:
    out = dict(book)
    for price, size in updates:
        if size < 0:
            raise ValueError("size cannot be negative")
        if size == 0:
            out.pop(price, None)
        else:
            out[price] = size
    return out


def normalise_broker(code: str) -> str:
    if code not in BROKER_CODES:
        raise KeyError(f"unknown broker code {code!r}")
    return BROKER_CODES[code]
