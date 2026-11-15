"""Execution simulator economics (FB-0185, FB-0187..0196, FB-0197, FB-0206).

Conventions
* A signal known at bar ``i`` (close) can fill no earlier than the open of bar ``i+1`` (next-bar default).
* Buys fill at the ask-side reference, sells at the bid-side reference; mid is never assumed tradable.
* Intrabar ambiguity: when one bar's range contains both the stop and the target, the adverse outcome is taken.
* Stops that gap through the level fill at the open (the unreachable level is not granted).
* Impact is square-root in participation and monotonic in size. Costs are explicit line items.
* Every fill carries a receipt with mode, price basis and timestamp, so a simulated fill is never presented as a
  broker fill.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

Side = Literal["BUY", "SELL"]
TIF = Literal["DAY", "IOC", "FOK", "GTC"]
Fidelity = Literal["BAR", "QUOTE", "TICK", "L2"]


class FillError(ValueError):
    pass


@dataclass(frozen=True)
class OhlcBar:
    open: float
    high: float
    low: float
    close: float
    volume: float


def next_bar_fill_index(signal_index: int, n_bars: int) -> int | None:
    """Earliest fill bar for a signal known at the close of ``signal_index``; None when no later bar exists."""
    nxt = signal_index + 1
    return nxt if nxt < n_bars else None


def resolve_stop_target(bar: OhlcBar, side: Side, stop: float, target: float) -> tuple[str, float]:
    """Return (outcome, price). Ambiguous same-bar collisions resolve adversely (stop first)."""
    if side == "BUY":  # long: stop below, target above
        if bar.open <= stop:
            return "STOP_GAP", bar.open
        hit_stop = bar.low <= stop
        hit_target = bar.high >= target
        if hit_stop and hit_target:
            return "STOP_AMBIGUOUS_ADVERSE", stop
        if hit_stop:
            return "STOP", stop
        if hit_target:
            return "TARGET", target
        return "NONE", bar.close
    if bar.open >= stop:  # short: stop above, target below
        return "STOP_GAP", bar.open
    hit_stop = bar.high >= stop
    hit_target = bar.low <= target
    if hit_stop and hit_target:
        return "STOP_AMBIGUOUS_ADVERSE", stop
    if hit_stop:
        return "STOP", stop
    if hit_target:
        return "TARGET", target
    return "NONE", bar.close


def resolve_stop_target_executable(
    bar: OhlcBar, position_side: Side, stop: float, target: float, spread: float
) -> tuple[str, float]:
    """Resolve a bar exit conservatively, then cross half the quoted spread to execute it.

    ``position_side`` is BUY for a long position and SELL for a short position. Ambiguous bars retain the
    adverse stop-first outcome from :func:`resolve_stop_target`; a long exits by selling at the bid and a short
    exits by buying at the ask. ``spread`` is the full bid/ask width in price units.
    """
    outcome, reference = resolve_stop_target(bar, position_side, stop, target)
    exit_side: Side = "SELL" if position_side == "BUY" else "BUY"
    return outcome, half_spread_fill(reference, exit_side, spread)


def half_spread_fill(reference: float, side: Side, spread: float) -> float:
    if spread < 0:
        raise FillError("spread must be non-negative")
    return reference + spread / 2 if side == "BUY" else reference - spread / 2


def square_root_impact(quantity: float, adv: float, coefficient: float = 0.1) -> float:
    """Fractional impact = c * sqrt(quantity / ADV). Monotonic in quantity; zero for zero quantity."""
    if adv <= 0:
        raise FillError("ADV must be positive; unknown liquidity is not zero liquidity")
    if quantity < 0 or coefficient < 0:
        raise FillError("quantity and coefficient must be non-negative")
    return coefficient * math.sqrt(quantity / adv)


@dataclass(frozen=True)
class Fill:
    quantity: int
    price: Decimal
    fees: Decimal
    fidelity: Fidelity
    price_basis: str
    fill_bar: int
    mode: str = "PAPER_SIMULATED"

    def receipt(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "fidelity": self.fidelity,
            "price_basis": self.price_basis,
            "fill_bar": self.fill_bar,
            "quantity": self.quantity,
            "price": str(self.price),
            "fees": str(self.fees),
        }


def fill_bar(
    bar: OhlcBar,
    *,
    index: int,
    side: Side,
    quantity: int,
    limit: float | None,
    tif: TIF,
    volume_cap: float,
) -> Fill | None:
    """Fill a market-or-limit order against one bar with partial-fill and TIF semantics.

    Returns None for no fill (e.g. FOK that cannot complete, or a limit not reached). Partial fills are capped by
    ``volume_cap`` for IOC/DAY; FOK requires the full quantity or nothing.
    """
    if quantity <= 0:
        raise FillError("quantity must be positive")
    if bar.volume <= 0:
        return None  # no volume: no fill is invented
    available = int(min(quantity, bar.volume, volume_cap))
    if limit is not None:
        reached = bar.low <= limit if side == "BUY" else bar.high >= limit
        if not reached:
            return None
        # a limit fills at the open when the open is already through it (price improvement), else at the limit
        if side == "BUY":
            price = bar.open if bar.open <= limit else limit
        else:
            price = bar.open if bar.open >= limit else limit
    else:
        price = bar.open
    if tif == "FOK":
        if available < quantity:
            return None
        filled = quantity
    else:
        filled = available
    if filled <= 0:
        return None
    return Fill(filled, Decimal(str(price)), Decimal(0), "BAR", "NEXT_OPEN_OR_LIMIT", index)


def charge_costs(notional: Decimal, *, commission_bps: Decimal, tax_bps: Decimal = Decimal(0)) -> Decimal:
    if commission_bps < 0 or tax_bps < 0:
        raise FillError("cost rates must be non-negative")
    return notional * (commission_bps + tax_bps) / Decimal(10_000)
