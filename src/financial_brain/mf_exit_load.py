"""Lot-aware mutual-fund exit-load calculation from explicit source terms."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date


class ExitLoadError(ValueError):
    pass


@dataclass(frozen=True)
class ExitLoadBand:
    holding_days_lt: int | None
    rate_pct: float

    def __post_init__(self) -> None:
        if self.holding_days_lt is not None and self.holding_days_lt <= 0:
            raise ExitLoadError("holding_days_lt must be positive")
        if not 0 <= self.rate_pct <= 100:
            raise ExitLoadError("rate_pct must be between 0 and 100")


@dataclass(frozen=True)
class PurchaseLot:
    lot_id: str
    units: float
    acquired_on: date
    schedule_known: bool
    bands: tuple[ExitLoadBand, ...] = ()
    source_sha256: str | None = None

    def __post_init__(self) -> None:
        if not self.lot_id or self.units <= 0:
            raise ExitLoadError("lot_id and positive units are required")
        if self.schedule_known and self.source_sha256 is None:
            raise ExitLoadError("known exit-load terms require source provenance")


def estimate_redemption(lots: list[PurchaseLot], units: float, *, nav: float,
                        redemption_on: date) -> dict:
    """Redeem FIFO from supplied lots and estimate charge per lot at current NAV.

    Unknown/missing schedules produce a null charge, not a zero charge. No lot ledger
    is mutated; callers persist resulting allocations as their own transaction record.
    """
    if units <= 0 or nav <= 0:
        raise ExitLoadError("redemption units and NAV must be positive")
    remaining = units
    allocations = []
    for lot in sorted(lots, key=lambda item: (item.acquired_on, item.lot_id)):
        if remaining <= 1e-10:
            break
        taken = min(lot.units, remaining)
        remaining -= taken
        days = (redemption_on - lot.acquired_on).days
        if days < 0:
            raise ExitLoadError("redemption date precedes lot purchase date")
        if not lot.schedule_known:
            rate = None
        else:
            active = [band for band in lot.bands
                      if band.holding_days_lt is None or days < band.holding_days_lt]
            rate = min(active, key=lambda band: band.holding_days_lt or 10**12).rate_pct if active else 0.0
        value = taken * nav
        charge = None if rate is None else value * rate / 100.0
        allocations.append({"lot_id": lot.lot_id, "units": taken, "holding_days": days,
                           "redemption_value": value, "exit_load_rate_pct": rate,
                           "exit_load_inr": charge, "source_sha256": lot.source_sha256})
    if remaining > 1e-8:
        raise ExitLoadError("redemption exceeds available lot units")
    return {"units": units, "nav": nav, "redemption_on": redemption_on,
            "redemption_type": "FULL" if abs(units - sum(l.units for l in lots)) <= 1e-8 else "PARTIAL",
            "allocations": allocations,
            "exit_load_inr": None if any(x["exit_load_inr"] is None for x in allocations)
            else sum(x["exit_load_inr"] for x in allocations)}
