"""The published-anomaly catalogue, and what this project can actually test of it."""
from __future__ import annotations

from . import catalogue as _c
from . import catalogue_events as _e

#: Every entry, in one list. The families are kept separate in source only for reading.
ALL = (_c.MOMENTUM + _c.RISK + _c.LIQUIDITY + _c.SEASONALITY + _c.VALUE
       + _c.QUALITY + _c.INVESTMENT + _e.EARNINGS + _e.EVENTS + _e.TECHNICAL)

READY = _c.READY
NEEDS_FUNDAMENTALS = _c.NEEDS_FUNDAMENTALS
NEEDS_EXTERNAL = _c.NEEDS_EXTERNAL


def by_status(status: str) -> list[dict]:
    return [f for f in ALL if f["status"] == status]


def by_family(family: str) -> list[dict]:
    return [f for f in ALL if f["family"] == family]


def families() -> dict[str, int]:
    out: dict[str, int] = {}
    for f in ALL:
        out[f["family"]] = out.get(f["family"], 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def summary() -> dict:
    """What the catalogue holds, and how much of it this project can reach."""
    ready = by_status(READY)
    return {
        "total": len(ALL),
        "ready": len(ready),
        "needs_fundamentals": len(by_status(NEEDS_FUNDAMENTALS)),
        "needs_external": len(by_status(NEEDS_EXTERNAL)),
        "families": families(),
        "sources": len({f["source"] for f in ALL}),
    }


def blocked_by_data() -> dict[str, list[str]]:
    """Which missing inputs block the most strategies - the shopping list, ranked."""
    out: dict[str, list[str]] = {}
    for f in ALL:
        if f["status"] == READY:
            continue
        for need in f["needs"]:
            out.setdefault(need, []).append(f["name"])
    return dict(sorted(out.items(), key=lambda kv: -len(kv[1])))
