"""Operations (FB-0540s-0560 subset): migrations, watchdog, health, logs, metrics, retention, rollback, sequencing,
archives, rotation, demo limits, signal regression, EOD summary, leak check, runbooks and journal integrity.

* ``plan_migrations`` gives the versions to apply going up, or to undo going down, in order.
* ``watchdog`` reports RESTART when the heartbeat is older than the timeout.
* ``health`` is HEALTHY only when every component reports healthy; otherwise DEGRADED with the failing names.
* ``correlated_entry`` builds a structured log entry that must carry a correlation id.
* ``lag_ms`` is event lag; a negative lag (event in the future) is refused.
* ``retention_action`` archives raw data past its retention and marks derived data past its retention as deletable.
* ``rollback_target`` returns the newest version before the current one that is not marked bad, or None.
* ``startup_order`` checks stage names are unique; shutdown is the reverse of startup.
* ``archive_layout`` refuses an offsite copy that shares a path with the local archive.
* ``rotation_due`` is true when a secret is older than its maximum age.
* ``demo_limits`` caps bars for machines under 16 GB.
* ``compare_signals`` lists signal names that were added, removed or changed between two nightly runs.
* ``eod_summary`` counts incidents, freezes and manual overrides for the day.
* ``memory_growth_ok`` checks a long replay did not grow memory beyond a limit.
* ``runbook_steps`` gives ordered remediation for a known alert, and escalates unknown alerts.
* ``journal_intact`` verifies each journal line's checksum and returns the first corrupt index, or None.
"""
from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import Any


def plan_migrations(current: int, target: int, available: Sequence[int]) -> list[int]:
    have = set(available)
    if target > current:
        steps = list(range(current + 1, target + 1))
    elif target < current:
        steps = list(range(current, target, -1))
    else:
        return []
    missing = [v for v in steps if v not in have]
    if missing:
        raise ValueError(f"missing migration versions {missing}")
    return steps


def watchdog(last_heartbeat_s: float, now_s: float, timeout_s: float) -> str:
    return "RESTART" if now_s - last_heartbeat_s > timeout_s else "OK"


def health(components: Mapping[str, bool]) -> tuple[str, list[str]]:
    failing = sorted(name for name, ok in components.items() if not ok)
    return ("DEGRADED" if failing else "HEALTHY"), failing


def correlated_entry(correlation_id: str, message: str) -> dict[str, str]:
    if not correlation_id:
        raise ValueError("correlation id is required")
    return {"correlation_id": correlation_id, "message": message}


def lag_ms(event_ts_ms: int, now_ms: int) -> int:
    if event_ts_ms > now_ms:
        raise ValueError("event timestamp is in the future")
    return now_ms - event_ts_ms


def retention_action(kind: str, age_days: int, raw_days: int, derived_days: int) -> str:
    if kind == "RAW":
        return "ARCHIVE" if age_days > raw_days else "KEEP"
    if kind == "DERIVED":
        return "DELETE_CANDIDATE" if age_days > derived_days else "KEEP"
    raise ValueError("kind must be RAW or DERIVED")


def rollback_target(versions: Sequence[str], current: str, bad: set[str]) -> str | None:
    if current not in versions:
        raise ValueError("current version is unknown")
    idx = versions.index(current)
    for v in reversed(versions[:idx]):
        if v not in bad:
            return v
    return None


def startup_order(stages: Sequence[str]) -> tuple[list[str], list[str]]:
    if len(set(stages)) != len(stages):
        raise ValueError("stage names must be unique")
    startup = list(stages)
    return startup, list(reversed(startup))


def archive_layout(local_root: str, offsite_root: str) -> None:
    if local_root == offsite_root:
        raise ValueError("offsite copy must not share a path with the local archive")


def rotation_due(created_day: int, today: int, max_days: int) -> bool:
    return today - created_day >= max_days


def demo_limits(ram_gb: float) -> dict[str, int]:
    if ram_gb <= 0:
        raise ValueError("ram must be positive")
    return {"max_bars": 50_000 if ram_gb < 16 else 500_000}


def compare_signals(previous: Mapping[str, Any], current: Mapping[str, Any]) -> list[str]:
    added = set(current) - set(previous)
    removed = set(previous) - set(current)
    changed = {k for k in set(previous) & set(current) if previous[k] != current[k]}
    return sorted(added | removed | changed)


def eod_summary(incidents: int, freezes: int, overrides: int) -> dict[str, int]:
    if min(incidents, freezes, overrides) < 0:
        raise ValueError("counts must be non-negative")
    return {"incidents": incidents, "freezes": freezes, "manual_overrides": overrides}


def memory_growth_ok(samples_mb: Sequence[float], max_growth_mb: float) -> bool:
    if len(samples_mb) < 2:
        raise ValueError("need at least two samples")
    return samples_mb[-1] - samples_mb[0] <= max_growth_mb


RUNBOOKS: dict[str, list[str]] = {
    "FEED_STALE": ["check feed connection", "restart feed", "page on-call if still stale after restart"],
    "QUEUE_BACKLOG": ["check consumer health", "pause new intake", "drain queue and resume"],
}


def runbook_steps(alert_code: str) -> list[str]:
    return list(RUNBOOKS.get(alert_code, ["escalate: unknown alert, no automated remediation"]))


def journal_intact(lines: Sequence[str]) -> int | None:
    for i, line in enumerate(lines):
        payload, _, sha = line.rpartition("|")
        if not payload or hashlib.sha256(payload.encode()).hexdigest()[:16] != sha:
            return i
    return None
