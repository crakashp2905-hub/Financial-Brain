"""Effective-dated tick sizes and price normalisation (FB-0086, FB-0192).

A tick rule applies from its effective date until the next rule. A day with no rule fails closed: there is no
silent fallback to a default tick. Normalisation maps a price onto the tick grid in a stated direction, so
normalising a normalised price changes nothing.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_EVEN, Decimal

from financial_brain.kernel.units import to_decimal

_MODES = {"DOWN": ROUND_FLOOR, "UP": ROUND_CEILING, "NEAREST": ROUND_HALF_EVEN}


class TickError(ValueError):
    pass


@dataclass(frozen=True)
class TickRule:
    effective_from: date
    tick: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "tick", to_decimal(self.tick))
        if self.tick <= 0:
            raise TickError("tick must be strictly positive")


class TickSchedule:
    def __init__(self, rules: list[TickRule]) -> None:
        if not rules:
            raise TickError("at least one tick rule is required")
        ordered = sorted(rules, key=lambda r: r.effective_from)
        dates = [r.effective_from for r in ordered]
        if len(set(dates)) != len(dates):
            raise TickError("two tick rules share an effective date")
        self._rules = ordered
        self._dates = dates

    def rule_on(self, day: date) -> TickRule:
        i = bisect_right(self._dates, day)
        if i == 0:
            raise TickError(f"no tick rule effective on {day.isoformat()}; refusing to guess")
        return self._rules[i - 1]

    def normalize(self, price: object, day: date, direction: str = "NEAREST") -> Decimal:
        if direction not in _MODES:
            raise TickError("direction must be DOWN, UP or NEAREST")
        p = to_decimal(price)
        if p <= 0:
            raise TickError("price must be strictly positive")
        tick = self.rule_on(day).tick
        steps = (p / tick).to_integral_value(rounding=_MODES[direction])
        result = steps * tick
        if result <= 0:
            raise TickError("price rounds to zero on the tick grid; no valid tradable price")
        return result
