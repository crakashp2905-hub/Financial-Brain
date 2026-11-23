"""Fail-closed mutual-fund redemption eligibility from explicit dated terms."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class RedemptionTerms:
    scheme_status: str | None
    status_as_of: date | None
    closed_end_maturity: date | None = None
    redemption_window_open: bool | None = None
    gating_max_units: float | None = None
    segregated_units: float = 0.0


@dataclass(frozen=True)
class RedemptionLot:
    units: float
    lockin_until: date | None = None


def check_redemption(*, terms: RedemptionTerms, lots: list[RedemptionLot], units: float,
                     as_of: date) -> dict:
    reasons: list[str] = []
    unknown: list[str] = []
    available = sum(lot.units for lot in lots)
    if units <= 0:
        reasons.append("REDEMPTION_UNITS_NOT_POSITIVE")
    if terms.scheme_status is None or terms.status_as_of is None:
        unknown.append("SCHEME_STATUS_UNKNOWN")
    elif terms.status_as_of > as_of:
        unknown.append("SCHEME_STATUS_NOT_YET_KNOWN")
    elif terms.scheme_status.upper() not in {"ACTIVE", "OPEN"}:
        reasons.append("SCHEME_NOT_OPEN_FOR_SUBSCRIPTION_OR_REDEMPTION")
    if terms.closed_end_maturity is not None and as_of < terms.closed_end_maturity:
        reasons.append("CLOSED_END_SCHEME_NOT_MATURED")
    if terms.redemption_window_open is False:
        reasons.append("REDEMPTION_WINDOW_CLOSED")
    elif terms.redemption_window_open is None:
        unknown.append("REDEMPTION_WINDOW_UNKNOWN")
    if units > max(0.0, available - terms.segregated_units):
        reasons.append("REQUEST_EXCEEDS_REDEEMABLE_UNITS")
    if terms.segregated_units < 0 or terms.segregated_units > available:
        reasons.append("INVALID_SEGREGATED_UNIT_BALANCE")
    if terms.gating_max_units is None:
        unknown.append("GATING_LIMIT_UNKNOWN")
    elif units > terms.gating_max_units:
        reasons.append("REDEMPTION_EXCEEDS_GATING_LIMIT")
    remaining = units
    locked_units = 0.0
    for lot in sorted(lots, key=lambda item: item.lockin_until or date.min):
        take = min(max(remaining, 0.0), lot.units)
        if lot.lockin_until is not None and lot.lockin_until > as_of:
            locked_units += take
        remaining -= take
    if remaining > 1e-8:
        reasons.append("REQUEST_EXCEEDS_AVAILABLE_LOTS")
    if locked_units > 1e-8:
        reasons.append("ELSS_OR_OTHER_LOT_LOCK_IN_ACTIVE")
    verdict = "REJECT" if reasons else ("REVIEW_REQUIRED" if unknown else "ELIGIBLE")
    return {"verdict": verdict, "reasons": reasons, "unknown_facts": unknown,
            "requested_units": units, "available_units": available,
            "locked_units_in_fifo_allocation": locked_units,
            "as_of": as_of}
