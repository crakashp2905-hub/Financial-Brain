"""Execution tools (FB-0215, FB-0217, FB-0221, FB-0223, FB-0226).

* ``OcoPair``: when one leg fills, the other is cancelled; a second fill on the other leg is refused.
* ``Mode``: MANUAL, SEMI_AUTO and PAPER_AUTO. Only PAPER_AUTO may submit automatically, and only to paper. Nothing here
  ever sends an external order.
* ``twap_slices`` and ``iceberg_slices`` split a parent quantity into child orders. Child quantities sum to the parent.
* ``validate_preview`` checks price against tick size and quantity against lot size and contract multiplier.
* ``slippage_bps`` measures a fill against arrival, midpoint and VWAP benchmarks, signed so that positive is worse.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum


@dataclass
class OcoPair:
    leg_a: str
    leg_b: str
    filled: str | None = None
    cancelled: set[str] | None = None

    def __post_init__(self) -> None:
        self.cancelled = set()

    def on_fill(self, leg: str) -> set[str]:
        if leg not in (self.leg_a, self.leg_b):
            raise ValueError("unknown leg")
        if self.filled is not None:
            raise ValueError(f"{self.filled} already filled; OCO allows one fill")
        self.filled = leg
        other = self.leg_b if leg == self.leg_a else self.leg_a
        assert self.cancelled is not None
        self.cancelled.add(other)
        return {other}


class Mode(Enum):
    MANUAL = "MANUAL"
    SEMI_AUTO = "SEMI_AUTO"
    PAPER_AUTO = "PAPER_AUTO"


def may_submit(mode: Mode, human_confirmed: bool, paper: bool) -> bool:
    """MANUAL never submits. SEMI_AUTO needs a human confirmation. PAPER_AUTO submits only to paper."""
    if mode is Mode.MANUAL:
        return False
    if mode is Mode.SEMI_AUTO:
        return human_confirmed and paper
    return paper


def twap_slices(total: int, n: int) -> list[int]:
    if total <= 0 or n <= 0:
        raise ValueError("total and n must be positive")
    base, rem = divmod(total, n)
    return [base + (1 if i < rem else 0) for i in range(n)]


def iceberg_slices(total: int, visible: int) -> list[int]:
    if total <= 0 or visible <= 0:
        raise ValueError("total and visible must be positive")
    out: list[int] = []
    left = total
    while left > 0:
        take = min(visible, left)
        out.append(take)
        left -= take
    return out


def validate_preview(price: Decimal, quantity: int, tick: Decimal, lot: int, multiplier: Decimal) -> list[str]:
    reasons: list[str] = []
    if tick <= 0 or price <= 0:
        reasons.append("tick and price must be positive")
    elif (price / tick) != (price / tick).to_integral_value():
        reasons.append(f"price not a multiple of tick {tick}")
    if lot <= 0 or quantity <= 0:
        reasons.append("quantity and lot must be positive")
    elif quantity % lot != 0:
        reasons.append(f"quantity not a multiple of lot {lot}")
    if multiplier <= 0:
        reasons.append("contract multiplier must be positive")
    return reasons


def slippage_bps(fill: Decimal, benchmark: Decimal, side: str) -> Decimal:
    """Positive means the fill was worse than the benchmark for this side."""
    if benchmark <= 0:
        raise ValueError("benchmark must be positive")
    move = (fill - benchmark) / benchmark * Decimal(10000)
    if side == "BUY":
        return move
    if side == "SELL":
        return -move
    raise ValueError("side must be BUY or SELL")
