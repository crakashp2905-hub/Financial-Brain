"""Fail-closed eligibility checks for mutual-fund paper redemptions."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from ..providers.mf_scheme_terms import ExitRestriction


class RedemptionConstraintError(ValueError):
    """A redemption conflicts with a known restriction or lacks required evidence."""


@dataclass(frozen=True)
class RedemptionEligibility:
    eligible: bool
    reasons: tuple[str, ...]
    eligible_units: Decimal | None


def resolve_redemption_eligibility(
    *,
    as_of: date,
    requested_units: Decimal | str | int,
    lot_units: Decimal | str | int,
    restrictions: tuple[ExitRestriction, ...] | None,
    lock_in_until: date | None = None,
    redemption_window_open: bool | None = None,
    gating_remaining_units: Decimal | str | int | None = None,
    segregated_portfolio_units: Decimal | str | int | None = None,
) -> RedemptionEligibility:
    """Return eligibility; unknown terms never become an assumed unrestricted sale.

    Values are supplied from source-pinned, point-in-time scheme/lot records by the
    caller. ``None`` restrictions means unknown, while an empty tuple means the source
    explicitly confirmed no restrictions.
    """
    def units(value: Decimal | str | int, label: str) -> Decimal:
        try:
            result = value if isinstance(value, Decimal) else Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise RedemptionConstraintError(f"{label} must be decimal") from exc
        if not result.is_finite() or result <= 0:
            raise RedemptionConstraintError(f"{label} must be finite and positive")
        return result

    requested = units(requested_units, "requested_units")
    available = units(lot_units, "lot_units")
    if requested > available:
        return RedemptionEligibility(False, ("REDEMPTION_EXCEEDS_AVAILABLE_UNITS",), Decimal(0))
    if restrictions is None:
        return RedemptionEligibility(False, ("RESTRICTIONS_UNKNOWN",), Decimal(0))

    flags = set(restrictions)
    reasons: list[str] = []
    allowed = requested
    if lock_in_until is not None and as_of < lock_in_until:
        reasons.append("LOT_LOCKED")
    if ExitRestriction.ELSS_LOCK_IN in flags and lock_in_until is None:
        reasons.append("ELSS_LOCK_IN_DATE_UNKNOWN")
    if ExitRestriction.CLOSED_TO_REDEMPTION in flags:
        reasons.append("CLOSED_TO_REDEMPTION")
    if ExitRestriction.CLOSED_ENDED_WINDOW in flags:
        if redemption_window_open is not True:
            reasons.append("REDEMPTION_WINDOW_CLOSED_OR_UNKNOWN")
    if ExitRestriction.GATING in flags:
        if gating_remaining_units is None:
            reasons.append("GATE_CAPACITY_UNKNOWN")
        else:
            gate_units = units(gating_remaining_units, "gating_remaining_units")
            allowed = min(allowed, gate_units)
            if requested > gate_units:
                reasons.append("GATING_LIMIT")
    if ExitRestriction.SEGREGATED_PORTFOLIO in flags:
        if segregated_portfolio_units is None:
            reasons.append("SEGREGATED_PORTFOLIO_ALLOCATION_UNKNOWN")
        else:
            segregated_units = units(segregated_portfolio_units, "segregated_portfolio_units")
            allowed = min(allowed, segregated_units)
            if requested > segregated_units:
                reasons.append("SEGREGATED_PORTFOLIO_LIMIT")
    if ExitRestriction.OTHER in flags:
        reasons.append("OTHER_RESTRICTION_REQUIRES_REVIEW")
    return RedemptionEligibility(not reasons, tuple(reasons), allowed if not reasons else Decimal(0))


__all__ = ["RedemptionConstraintError", "RedemptionEligibility", "resolve_redemption_eligibility"]
