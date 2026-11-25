"""Exit plan details: closing sides, price basis, trailing and partial exits, linked exit records.

Checklist 4A/19: FB-0453, FB-0455, FB-0456, FB-0466, FB-0488, FB-1904, FB-1906, FB-1908.

* Closing side is derived, never typed: BUY closes with SELL_TO_CLOSE, SELL_SHORT with BUY_TO_COVER;
  a spread closes every leg for its own quantity.
* Every level has a basis (underlying, ETF quote, option premium, spread value, published NAV);
  levels on different bases are never compared.
* A ladder of partial targets, a breakeven move and a trailing stop are versioned rules. Each stop
  revision is a new record; the original plan is kept.
* Every executed exit becomes an independent ``ExitSignal`` linked to its entry by ids.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum


class PlanError(ValueError):
    pass


class Basis(str, Enum):
    UNDERLYING = "UNDERLYING"
    ETF_QUOTE = "ETF_QUOTE"
    OPTION_PREMIUM = "OPTION_PREMIUM"
    SPREAD_VALUE = "SPREAD_VALUE"
    PUBLISHED_NAV = "PUBLISHED_NAV"
    EQUITY_QUOTE = "EQUITY_QUOTE"


CLOSE_SIDE = {"BUY": "SELL_TO_CLOSE", "SELL_SHORT": "BUY_TO_COVER"}


def closing_side(entry_side: str) -> str:
    if entry_side not in CLOSE_SIDE:
        raise PlanError(f"no closing side for {entry_side!r}")
    return CLOSE_SIDE[entry_side]


@dataclass(frozen=True)
class Leg:
    token: str
    side: str
    quantity: int


def close_legs(legs: list[Leg], active_tokens: set[str]) -> list[Leg]:
    """Closing orders for every leg, each for its own quantity, on exact active contracts (FB-1904)."""
    out = []
    for leg in legs:
        if leg.token not in active_tokens:
            raise PlanError(f"leg {leg.token} is not an active contract")
        out.append(Leg(leg.token, "SELL" if leg.side == "BUY" else "BUY", leg.quantity))
    return out


@dataclass(frozen=True)
class Level:
    """A price level with its basis and type. No bare numbers (FB-0455/0456)."""

    price: Decimal
    basis: Basis
    kind: str                   # STOP, TARGET, TRAIL, BREAKEVEN


def same_basis(*levels: Level) -> None:
    if len({lv.basis for lv in levels}) > 1:
        raise PlanError("levels on different price bases cannot be compared")


@dataclass(frozen=True)
class ExitRule:
    version: str
    targets: tuple[tuple[Decimal, Decimal], ...]   # (price, fraction of original quantity)
    breakeven_after_first: bool
    trail_distance: Decimal | None


@dataclass
class ManagedExit:
    position_id: str
    entry: Decimal
    quantity: int
    stop: Level
    rule: ExitRule
    remaining: int = 0
    stops: list[tuple[datetime, Decimal, str]] = field(default_factory=list)
    hit: set[int] = field(default_factory=set)
    high: Decimal = Decimal(0)

    def __post_init__(self) -> None:
        if sum(f for _, f in self.rule.targets) > 1:
            raise PlanError("partial targets exceed the position")
        self.remaining = self.quantity
        self.high = self.entry
        self.stops.append((datetime.min.replace(tzinfo=None), self.stop.price, "ORIGINAL"))

    @property
    def current_stop(self) -> Decimal:
        return self.stops[-1][1]

    def _move_stop(self, at: datetime, price: Decimal, why: str) -> None:
        if price > self.current_stop:          # long stops only ratchet up
            self.stops.append((at, price, why))

    def on_price(self, at: datetime, price: Decimal) -> list[tuple[str, int, Decimal]]:
        """Long position. Returns exits as (reason, quantity, price); never more than remaining."""
        exits: list[tuple[str, int, Decimal]] = []
        if self.remaining == 0:
            return exits
        if price <= self.current_stop:
            exits.append(("STOP", self.remaining, price))
            self.remaining = 0
            return exits
        for i, (tp, frac) in enumerate(self.rule.targets):
            if i in self.hit or price < tp:
                continue
            qty = min(self.remaining, int(self.quantity * frac))
            if qty > 0:
                exits.append((f"TP{i + 1}", qty, price))
                self.remaining -= qty
            self.hit.add(i)
            if i == 0 and self.rule.breakeven_after_first:
                self._move_stop(at, self.entry, "BREAKEVEN")
        if self.rule.trail_distance is not None and price > self.high:
            self.high = price
            self._move_stop(at, price - self.rule.trail_distance, "TRAIL")
        return exits


@dataclass(frozen=True)
class ExitSignal:
    exit_id: str
    closes_position_id: str
    entry_signal_id: str
    reason: str
    trigger_price: Decimal
    fill_price: Decimal | None
    quantity: int
    fees: Decimal
    at: datetime

    def pnl(self, entry: Decimal, long: bool = True) -> Decimal | None:
        if self.fill_price is None:
            return None
        d = self.fill_price - entry if long else entry - self.fill_price
        return d * self.quantity - self.fees


def paired_report(entry_signal_id: str, entry_at: datetime, entry_fill: Decimal, thesis: str,
                  original_stop: Decimal, original_target: Decimal,
                  revisions: list[tuple[datetime, Decimal, str]], exits: list[ExitSignal]) -> dict:
    """Machine-readable paired lifecycle record (FB-0488)."""
    filled = [e for e in exits if e.fill_price is not None]
    net = sum((e.pnl(entry_fill) or Decimal(0)) for e in filled)
    risk = entry_fill - original_stop
    qty = sum(e.quantity for e in filled)
    return {
        "entry_signal_id": entry_signal_id, "entry_at": entry_at.isoformat(), "thesis": thesis,
        "entry_fill": str(entry_fill), "original_stop": str(original_stop),
        "original_target": str(original_target),
        "revisions": [(a.isoformat() if a.tzinfo else "original", str(p), w) for a, p, w in revisions],
        "exits": [{"id": e.exit_id, "reason": e.reason, "at": e.at.isoformat(),
                   "fill": None if e.fill_price is None else str(e.fill_price), "qty": e.quantity}
                  for e in exits],
        "net_pnl": str(net),
        "r_multiple": str(net / (risk * qty)) if risk > 0 and qty else None,
        "unresolved": any(e.fill_price is None for e in exits),
    }
