"""Features, filters and journals (FB-0366, FB-0368, FB-0374, FB-0375, FB-0380).

* ``ThesisJournal`` is append-only. Each entry is kept with its date; amendments are new entries, never edits.
* ``recommend`` returns a recommendation state, not a forced trade: NO_TRADE when the signal is weak, and
  INSUFFICIENT_DATA when the history is too short. It never invents a recommendation.
* ``imbalance`` gives order-flow imbalance from bid and ask depth, in [-1, 1]. Empty depth gives None.
* ``entry_filter`` refuses an intraday entry when the spread is too wide or the queue ahead is too deep.
* ``side_costs`` applies long and short specific fees, borrow and restriction flags. Short sales need borrow
  availability, and a restricted side is refused.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal


@dataclass
class ThesisJournal:
    entries: list[tuple[str, str]] = field(default_factory=list)

    def add(self, day: str, text: str) -> None:
        if not text.strip():
            raise ValueError("empty thesis entry")
        self.entries.append((day, text))

    def history(self) -> list[tuple[str, str]]:
        return list(self.entries)


def recommend(signal: float | None, history_days: int, min_history: int, threshold: float) -> str:
    if signal is None or history_days < min_history:
        return "INSUFFICIENT_DATA"
    if abs(signal) < threshold:
        return "NO_TRADE"
    return "LONG" if signal > 0 else "SHORT"


def imbalance(bid_depth: Sequence[int], ask_depth: Sequence[int]) -> float | None:
    bid = sum(bid_depth)
    ask = sum(ask_depth)
    total = bid + ask
    if total == 0:
        return None
    return (bid - ask) / total


def entry_filter(spread: Decimal, mid: Decimal, max_spread_bps: Decimal, queue_ahead: int, max_queue: int) -> list[str]:
    reasons: list[str] = []
    if mid <= 0:
        raise ValueError("mid must be positive")
    if spread / mid * Decimal(10000) > max_spread_bps:
        reasons.append("spread too wide")
    if queue_ahead > max_queue:
        reasons.append("queue ahead too deep")
    return reasons


@dataclass(frozen=True)
class SideRules:
    fee_bps: Decimal
    borrow_bps_per_day: Decimal
    restricted: bool
    borrow_available: bool


def side_costs(side: str, notional: Decimal, days: int, rules: SideRules) -> Decimal:
    if side not in ("LONG", "SHORT"):
        raise ValueError("side must be LONG or SHORT")
    if rules.restricted:
        raise ValueError("side is restricted for this instrument")
    fee = notional * rules.fee_bps / Decimal(10000)
    if side == "LONG":
        return fee
    if not rules.borrow_available:
        raise ValueError("no borrow available for short")
    return fee + notional * rules.borrow_bps_per_day * Decimal(days) / Decimal(10000)
