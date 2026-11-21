"""Cooldowns, concurrency limits and duplicate-trigger protection (FB-0129).

A trigger carries a deterministic id derived from strategy, instrument and bar. Triggers already seen are ignored, so
a reconnect or a replay cannot fire the same trigger twice.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class StrategyState:
    cooldown_bars: int
    max_concurrent: int
    open_positions: set[str] = field(default_factory=set)
    last_entry_bar: dict[str, int] = field(default_factory=dict)
    seen_triggers: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        if self.cooldown_bars < 0 or self.max_concurrent < 1:
            raise ValueError("cooldown must be >= 0 and max_concurrent >= 1")

    def trigger(self, trigger_id: str, instrument: str, bar: int) -> bool:
        """True if an entry is allowed now; records it. False for duplicates, cooldown and capacity."""
        if trigger_id in self.seen_triggers:
            return False
        self.seen_triggers.add(trigger_id)
        last = self.last_entry_bar.get(instrument)
        if last is not None and bar - last < self.cooldown_bars:
            return False
        if instrument not in self.open_positions and len(self.open_positions) >= self.max_concurrent:
            return False
        self.open_positions.add(instrument)
        self.last_entry_bar[instrument] = bar
        return True

    def close(self, instrument: str) -> None:
        self.open_positions.discard(instrument)


def trigger_id(strategy: str, instrument: str, bar: int) -> str:
    return f"{strategy}|{instrument}|{bar}"
