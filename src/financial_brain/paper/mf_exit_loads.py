"""Per-purchase-lot mutual-fund exit-load simulation for full/partial redemption."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from ..providers.mf_scheme_terms import ExitLoadBand
from ..providers.mf_scheme_terms import MFChargeRule

PAISE = Decimal("0.01")


def calculate_transaction_charges(
    rules: tuple[MFChargeRule, ...] | None,
    *,
    transaction: str,
    notional_inr: Decimal | str | int,
) -> tuple[Decimal, tuple[tuple[str, Decimal], ...]]:
    """Calculate source-disclosed percentage charges; unknown schedules fail closed."""
    if transaction not in {"SUBSCRIBE", "REDEEM"}:
        raise ExitLoadError("unsupported MF charge transaction")
    if rules is None:
        raise ExitLoadError("effective-dated MF transaction charge schedule is unknown")
    notional = _positive(notional_inr, "charge notional", allow_zero=True)
    breakdown: list[tuple[str, Decimal]] = []
    for rule in rules:
        if rule.applies_to == transaction:
            amount = (notional * Decimal(rule.rate_percent) / Decimal(100)).quantize(
                PAISE, rounding=ROUND_HALF_UP,
            )
            breakdown.append((rule.code, amount))
    return sum((amount for _, amount in breakdown), Decimal(0)), tuple(breakdown)


class ExitLoadError(ValueError):
    """A redemption cannot be costed safely from the supplied lot evidence."""


def _positive(value: Decimal | str | int, label: str, *, allow_zero: bool = False) -> Decimal:
    try:
        number = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ExitLoadError(f"{label} must be a decimal") from exc
    if not number.is_finite() or number < 0 or (number == 0 and not allow_zero):
        raise ExitLoadError(f"{label} must be {'non-negative' if allow_zero else 'positive'}")
    return number


@dataclass(frozen=True)
class PurchaseLot:
    lot_id: str
    allotted_on: date
    units: Decimal | str | int
    exit_loads: tuple[ExitLoadBand, ...] | None

    def __post_init__(self) -> None:
        if not self.lot_id.strip():
            raise ExitLoadError("lot_id is required")
        object.__setattr__(self, "units", _positive(self.units, "lot units"))


@dataclass(frozen=True)
class ExitLoadAllocation:
    lot_id: str
    units_redeemed: Decimal
    units_remaining: Decimal
    holding_days: int
    rate_percent: Decimal
    exit_load_inr: Decimal


@dataclass(frozen=True)
class ExitLoadResult:
    redeemed_units: Decimal
    nav_per_unit: Decimal
    gross_redemption_inr: Decimal
    exit_load_inr: Decimal
    net_redemption_inr: Decimal
    allocations: tuple[ExitLoadAllocation, ...]


def calculate_exit_loads(
    lots: tuple[PurchaseLot, ...],
    *,
    redemption_date: date,
    redemption_units: Decimal | str | int,
    nav_per_unit: Decimal | str | int,
) -> ExitLoadResult:
    """Allocate redemption units FIFO and charge the source-pinned schedule per lot.

    Holding periods use elapsed calendar days. If a source uses a different convention,
    callers must normalize it before invoking this function. ``None`` means unknown and
    blocks valuation; an empty schedule is an explicit zero-load declaration.
    """
    units_to_redeem = _positive(redemption_units, "redemption units")
    nav = _positive(nav_per_unit, "NAV per unit")
    if not lots:
        raise ExitLoadError("at least one purchase lot is required")
    if len({lot.lot_id for lot in lots}) != len(lots):
        raise ExitLoadError("purchase lot IDs must be unique")
    if any(redemption_date < lot.allotted_on for lot in lots):
        raise ExitLoadError("redemption date precedes a purchase-lot allotment date")
    ordered = sorted(lots, key=lambda lot: (lot.allotted_on, lot.lot_id))
    available = sum((Decimal(lot.units) for lot in ordered), Decimal(0))
    if units_to_redeem > available:
        raise ExitLoadError("redemption units exceed available position units")

    remaining = units_to_redeem
    allocations: list[ExitLoadAllocation] = []
    unrounded_load = Decimal(0)
    for lot in ordered:
        if remaining == 0:
            break
        taken = min(remaining, Decimal(lot.units))
        remaining -= taken
        if lot.exit_loads is None:
            raise ExitLoadError(f"exit-load schedule is unknown for lot {lot.lot_id}")
        holding_days = (redemption_date - lot.allotted_on).days
        thresholds = [band.days_from_purchase for band in lot.exit_loads]
        if thresholds != sorted(set(thresholds)):
            raise ExitLoadError(f"exit-load schedule is ambiguous for lot {lot.lot_id}")
        eligible = [band for band in lot.exit_loads if band.days_from_purchase <= holding_days]
        rate = max(eligible, key=lambda band: band.days_from_purchase).rate_percent \
            if eligible else Decimal(0)
        fee = taken * nav * rate / Decimal(100)
        unrounded_load += fee
        allocations.append(ExitLoadAllocation(
            lot_id=lot.lot_id, units_redeemed=taken,
            units_remaining=Decimal(lot.units) - taken, holding_days=holding_days,
            rate_percent=rate, exit_load_inr=fee,
        ))

    gross = units_to_redeem * nav
    load = unrounded_load.quantize(PAISE, rounding=ROUND_HALF_UP)
    return ExitLoadResult(
        redeemed_units=units_to_redeem, nav_per_unit=nav,
        gross_redemption_inr=gross, exit_load_inr=load,
        net_redemption_inr=gross - load, allocations=tuple(allocations),
    )


__all__ = [
    "ExitLoadAllocation", "ExitLoadError", "ExitLoadResult", "PurchaseLot",
    "calculate_exit_loads", "calculate_transaction_charges",
]
