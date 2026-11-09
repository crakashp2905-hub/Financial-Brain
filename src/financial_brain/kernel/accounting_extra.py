"""Accounting and margin extras (FB-0234, FB-0238, FB-0239, FB-0245, FB-0277).

* ``borrow_interest`` charges simple interest on negative cash only. Positive cash earns nothing here.
* ``fx_pnl_split`` splits reporting-currency P&L into a price part and an FX part. The parts sum to the total.
* ``margin_requirement`` and ``margin_call`` apply initial and maintenance rates. A call is an event, not a state change.
* ``EodSnapshot`` freezes positions and cash for a date. A snapshot for an already-frozen date is refused.
* ``futures_liquidation_price`` and ``stress_breach`` test a leveraged long or short against an adverse shock.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any


def borrow_interest(cash: Decimal, annual_rate: Decimal, days: int) -> Decimal:
    if cash >= 0:
        return Decimal("0")
    return -cash * annual_rate * Decimal(days) / Decimal(365)


def fx_pnl_split(
    quantity: Decimal,
    price_entry: Decimal,
    price_exit: Decimal,
    fx_entry: Decimal,
    fx_exit: Decimal,
) -> dict[str, Decimal]:
    """Price P&L at entry FX, FX P&L on exit price. Price plus FX equals the total reporting-currency P&L."""
    price_pnl = quantity * (price_exit - price_entry) * fx_entry
    fx_pnl = quantity * price_exit * (fx_exit - fx_entry)
    total = quantity * price_exit * fx_exit - quantity * price_entry * fx_entry
    return {"price": price_pnl, "fx": fx_pnl, "total": total}


def margin_requirement(notional: Decimal, rate: Decimal) -> Decimal:
    if rate <= 0 or rate > 1:
        raise ValueError("margin rate must be in (0, 1]")
    return abs(notional) * rate


@dataclass(frozen=True)
class MarginCallEvent:
    date: str
    equity: Decimal
    maintenance: Decimal


def margin_call(date: str, equity: Decimal, maintenance: Decimal) -> MarginCallEvent | None:
    if equity < maintenance:
        return MarginCallEvent(date, equity, maintenance)
    return None


@dataclass
class EodSnapshot:
    frozen: dict[str, dict[str, Any]] = field(default_factory=dict)

    def freeze(self, day: str, positions: dict[str, str], cash: str) -> str:
        if day in self.frozen:
            raise ValueError(f"EOD snapshot for {day} already frozen")
        body = {"positions": positions, "cash": cash}
        digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        self.frozen[day] = {**body, "digest": digest}
        return digest


def futures_liquidation_price(entry: Decimal, leverage: Decimal, side: str) -> Decimal:
    """Price move that wipes out initial margin: 1/leverage of entry, adverse to the side."""
    if leverage <= 1:
        raise ValueError("leverage must exceed 1")
    move = entry / leverage
    if side == "LONG":
        return entry - move
    if side == "SHORT":
        return entry + move
    raise ValueError("side must be LONG or SHORT")


def stress_breach(entry: Decimal, leverage: Decimal, side: str, shock_pct: Decimal) -> bool:
    """True if an adverse shock of ``shock_pct`` (0.10 = 10%) reaches the liquidation price."""
    liq = futures_liquidation_price(entry, leverage, side)
    shocked = entry * (Decimal(1) - shock_pct) if side == "LONG" else entry * (Decimal(1) + shock_pct)
    return shocked <= liq if side == "LONG" else shocked >= liq
