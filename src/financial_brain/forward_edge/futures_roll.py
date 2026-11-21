"""Dated futures roll economics: basis, lot change, margin, financing and MTM (FE-0063)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date


class FuturesRollError(ValueError):
    """Missing or inconsistent futures contract transition inputs."""


@dataclass(frozen=True)
class FuturesContractMark:
    symbol: str
    expiry: date
    lot_size: int
    price: float
    observed_on: date
    margin_per_lot: float


def roll_futures_position(*, old: FuturesContractMark, new: FuturesContractMark,
                          lots: int, prior_settlement: float, financing_rate_annual: float,
                          calendar_days: int) -> dict:
    if not old.symbol or not new.symbol or old.symbol == new.symbol:
        raise FuturesRollError("roll requires distinct contract symbols")
    if old.expiry >= new.expiry or old.lot_size < 1 or new.lot_size < 1 or lots < 1:
        raise FuturesRollError("invalid expiry, lot size or position size")
    if old.observed_on != new.observed_on:
        raise FuturesRollError("front and back contract marks must share an observation date")
    if old.observed_on >= old.expiry or old.observed_on >= new.expiry:
        raise FuturesRollError("roll marks must precede both expiries")
    values = (old.price, new.price, prior_settlement, financing_rate_annual,
              old.margin_per_lot, new.margin_per_lot)
    if any(value != value or abs(value) == float("inf") for value in values):
        raise FuturesRollError("prices, margin and rates must be finite")
    if min(old.price, new.price, prior_settlement, old.margin_per_lot, new.margin_per_lot) <= 0:
        raise FuturesRollError("prices and margins must be positive")
    if financing_rate_annual < 0 or calendar_days < 0:
        raise FuturesRollError("financing rate and calendar days must be non-negative")
    old_quantity, new_quantity = lots * old.lot_size, lots * new.lot_size
    basis = new.price - old.price  # negative basis (back below front) is preserved
    old_mtm = (old.price - prior_settlement) * old_quantity
    margin_before = lots * old.margin_per_lot
    margin_after = lots * new.margin_per_lot
    financing = old.price * old_quantity * financing_rate_annual * calendar_days / 365.0
    return {"roll_date": old.observed_on.isoformat(), "old_contract": old.symbol,
            "new_contract": new.symbol, "old_expiry": old.expiry.isoformat(),
            "new_expiry": new.expiry.isoformat(), "basis_old_to_new": basis,
            "basis_sign": "NEGATIVE" if basis < 0 else "POSITIVE" if basis > 0 else "FLAT",
            "old_quantity": old_quantity, "new_quantity": new_quantity,
            "quantity_delta": new_quantity - old_quantity, "pre_roll_mtm_inr": old_mtm,
            "margin_before_inr": margin_before, "margin_after_inr": margin_after,
            "margin_change_inr": margin_after - margin_before,
            "financing_cost_inr": financing, "mark_source_status": "CALLER_SUPPLIED_NOT_AUTHENTICATED"}
