"""Futures roll, basis, lot changes, margin and financing (FE-0063). Pure arithmetic on supplied prices."""
from __future__ import annotations

from decimal import Decimal


class FuturesError(ValueError):
    pass


def roll(*, lots: int, lot_size_old: int, lot_size_new: int, near_price: Decimal, far_price: Decimal,
         fee_per_leg: Decimal) -> dict:
    """Close the near contract and open the far one. Basis is paid by a long in contango and received in backwardation."""
    if lots <= 0 or lot_size_old <= 0 or lot_size_new <= 0 or near_price <= 0 or far_price <= 0:
        raise FuturesError("invalid roll inputs")
    units = lots * lot_size_old
    basis = far_price - near_price
    new_lots = units // lot_size_new                      # whole lots only
    residual_units = units - new_lots * lot_size_new      # exposure that cannot be carried after a lot-size change
    roll_cost = basis * units + 2 * fee_per_leg           # long pays far-near; negative basis is a credit
    return {"basis": basis, "units_before": units, "new_lots": new_lots, "residual_units": residual_units,
            "roll_cost": roll_cost, "credit": basis < 0}


def variation_margin(settles: list[Decimal], units: int, side: str = "LONG") -> list[Decimal]:
    sign = 1 if side == "LONG" else -1
    return [(b - a) * units * sign for a, b in zip(settles, settles[1:])]


def financing_cost(blocked_margin: Decimal, annual_rate: Decimal, days: int) -> Decimal:
    if blocked_margin < 0 or days < 0:
        raise FuturesError("invalid financing inputs")
    return blocked_margin * annual_rate * Decimal(days) / Decimal(365)
