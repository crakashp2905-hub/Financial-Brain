"""Sandboxes, memory, retrieval, inbox and notification controls (FB-0484, FB-0485, FB-0496, FB-0499, FB-0504, FB-0505,
FB-0514, FB-0518, FB-0527, FB-0533, FB-0534, FB-0535, FB-0537).

* ``ips_value`` is an inverse-propensity estimate for a contextual bandit, using logged propensities.
* ``suggest_only`` returns a research suggestion that is never executed; the sandbox has no order authority.
* ``shadow_agreement`` compares live and shadow decisions. Shadow records are never sent.
* ``lineage_report`` lists everything a decision depends on, using the lineage walk from batch 20 ops.
* ``adapter_available`` reports whether an optional module is installed, without importing it.
* ``parse_plan`` turns "buy 10 NIFTY at 100" into a typed paper plan; anything else is refused.
* ``MemoryStore`` keeps session notes for a retention period and deletes on request.
* ``search_docs`` returns offline documents that contain every query word.
* ``Inbox`` stores local messages and tracks which are unread.
* ``replay_alerts`` returns the indices where a price first crosses above a threshold.
* ``NotificationSettings`` are off by default for every strategy.
* ``keep_latest`` returns the artifacts to delete when keeping only the newest ``n``.
* ``call_adapter`` refuses any external adapter that is not explicitly enabled in config.
"""
from __future__ import annotations

import importlib.util
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from financial_brain.kernel.batch20_ops import ancestors


def ips_value(rewards: Sequence[float], logged_propensity: Sequence[float], target_prob: Sequence[float]) -> float:
    if not rewards or not (len(rewards) == len(logged_propensity) == len(target_prob)):
        raise ValueError("rewards, propensities and targets must align and be non-empty")
    total = 0.0
    for r, p_log, p_tgt in zip(rewards, logged_propensity, target_prob, strict=True):
        if p_log <= 0:
            raise ValueError("logged propensity must be positive")
        total += r * p_tgt / p_log
    return total / len(rewards)


def suggest_only(action: str) -> dict[str, Any]:
    return {"action": action, "executed": False}


def shadow_agreement(live: Sequence[str], shadow: Sequence[str]) -> dict[str, Any]:
    if len(live) != len(shadow) or not live:
        raise ValueError("decision lists must align and be non-empty")
    same = sum(1 for a, b in zip(live, shadow, strict=True) if a == b)
    records = [{"shadow_action": b, "sent": False} for b in shadow]
    return {"agreement": same / len(live), "records": records}


def lineage_report(parents: Mapping[str, Sequence[str]], decision: str) -> list[str]:
    return sorted(ancestors(parents, decision))


def adapter_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def parse_plan(text: str) -> dict[str, Any]:
    parts = text.lower().split()
    if len(parts) != 5 or parts[0] not in ("buy", "sell") or parts[3] != "at" or not parts[2].isalpha():
        raise ValueError("plan must read: buy|sell <qty> <SYMBOL> at <price>")
    try:
        qty = int(parts[1])
        price = float(parts[4])
    except ValueError as exc:
        raise ValueError("quantity and price must be numbers") from exc
    if qty <= 0 or price <= 0:
        raise ValueError("quantity and price must be positive")
    return {"side": parts[0].upper(), "qty": qty, "symbol": parts[2].upper(), "price": price, "mode": "PAPER"}


@dataclass
class MemoryStore:
    retain_days: int
    _items: dict[str, tuple[int, Any]] = field(default_factory=dict)

    def put(self, key: str, value: Any, day: int) -> None:
        self._items[key] = (day, value)

    def get(self, key: str) -> Any:
        return self._items[key][1]

    def delete(self, key: str) -> None:
        self._items.pop(key, None)

    def prune(self, today: int) -> list[str]:
        gone = [k for k, (day, _) in self._items.items() if today - day > self.retain_days]
        for k in gone:
            del self._items[k]
        return sorted(gone)


def search_docs(docs: Mapping[str, str], query: str) -> list[str]:
    words = query.lower().split()
    return sorted(name for name, text in docs.items() if all(w in text.lower() for w in words))


@dataclass
class Inbox:
    messages: list[str] = field(default_factory=list)
    read: set[int] = field(default_factory=set)

    def add(self, text: str) -> None:
        self.messages.append(text)

    def unread(self) -> list[str]:
        return [m for i, m in enumerate(self.messages) if i not in self.read]

    def mark_read(self, index: int) -> None:
        if not 0 <= index < len(self.messages):
            raise IndexError("no such message")
        self.read.add(index)


def replay_alerts(prices: Sequence[float], threshold: float) -> list[int]:
    return [i for i in range(1, len(prices)) if prices[i - 1] <= threshold < prices[i]]


@dataclass
class NotificationSettings:
    enabled: dict[str, bool] = field(default_factory=dict)

    def is_on(self, strategy: str) -> bool:
        return self.enabled.get(strategy, False)

    def set_on(self, strategy: str, on: bool) -> None:
        self.enabled[strategy] = on


def keep_latest(artifacts: Sequence[tuple[str, int]], n: int) -> list[str]:
    if n < 0:
        raise ValueError("n must be non-negative")
    ordered = sorted(artifacts, key=lambda a: a[1])
    cut = max(0, len(ordered) - n)
    return [name for name, _ in ordered[:cut]]


def call_adapter(name: str, config: Mapping[str, bool]) -> str:
    if not config.get(name, False):
        raise PermissionError(f"adapter {name} is disabled by default; enable it explicitly in config")
    return "CALLED"
