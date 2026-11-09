"""Alerts, automation and workflows (FB-0521, FB-0522, FB-0523, FB-0530, FB-0531, FB-0536, FB-0538).

* ``match_rules`` returns the names of watchlist rules an item matches. A rule field of None is a wildcard.
* ``is_due`` is a time alert that fires only inside the exchange session on a weekday.
* ``evaluate_alert`` checks price, volume and indicator alerts against a threshold; unknown kinds are refused.
* ``paper_submit`` submits to paper only under the notional cap and when no risk veto is set.
* ``run_workflow`` runs steps in order and stops at the first failure, reporting what finished.
* ``backoff_schedule`` gives bounded exponential retry delays and a dead-letter flag after the last attempt.
* ``days_to_expiry_reminder`` flags an expiry reminder when the expiry is within the window.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

ALERT_KINDS = {"PRICE", "VOLUME", "INDICATOR"}


def match_rules(rules: Sequence[Mapping[str, Any]], item: Mapping[str, Any]) -> list[str]:
    matched = []
    for rule in rules:
        ok = all(v is None or item.get(k) == v for k, v in rule.items() if k != "name")
        if ok:
            matched.append(str(rule["name"]))
    return matched


def is_due(now_min: int, open_min: int, close_min: int, weekday: int) -> bool:
    if not 0 <= weekday <= 6:
        raise ValueError("weekday must be 0-6")
    return weekday < 5 and open_min <= now_min <= close_min


def evaluate_alert(kind: str, value: float, threshold: float) -> bool:
    if kind not in ALERT_KINDS:
        raise ValueError(f"unknown alert kind {kind!r}")
    return value >= threshold


def paper_submit(notional: float, cap: float, risk_veto: bool) -> str:
    if risk_veto:
        return "VETOED"
    if notional > cap:
        return "CAP_EXCEEDED"
    return "SUBMITTED_PAPER"


def run_workflow(steps: Sequence[tuple[str, Callable[[], Any]]]) -> tuple[list[str], str | None]:
    completed: list[str] = []
    for name, fn in steps:
        try:
            fn()
        except Exception:
            return completed, name
        completed.append(name)
    return completed, None


def backoff_schedule(base: float, attempts: int, cap: float) -> dict[str, Any]:
    if base <= 0 or attempts < 1 or cap <= 0:
        raise ValueError("base, attempts and cap must be positive")
    delays = [min(cap, base * 2**i) for i in range(attempts)]
    return {"delays": delays, "dead_letter": True}


def days_to_expiry_reminder(today: int, expiry: int, window_days: int) -> bool:
    return 0 <= expiry - today <= window_days
