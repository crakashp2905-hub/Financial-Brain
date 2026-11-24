"""Keep observed NAV returns separate from future expense-ratio projections."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation


class MfReturnError(ValueError):
    """Invalid NAV return or forward expense assumption."""


def _decimal(value: Decimal | str | int, name: str) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise MfReturnError(f"{name} must be decimal") from exc
    if not result.is_finite():
        raise MfReturnError(f"{name} must be finite")
    return result


@dataclass(frozen=True)
class ObservedNavReturn:
    start_date: date
    end_date: date
    start_nav: Decimal
    end_nav: Decimal
    nav_change_percent: Decimal
    basis: str = "OBSERVED_NAV_RATIO;TER_ALREADY_REFLECTED"


@dataclass(frozen=True)
class ForwardNetProjection:
    gross_annual_return_percent: Decimal
    ter_annual_percent: Decimal
    years: int
    projected_net_return_percent: Decimal
    basis: str = "FORWARD_ONLY;TER_APPLIED_SEPARATELY"


def observed_nav_change(
    start_nav: Decimal | str | int,
    end_nav: Decimal | str | int,
    *,
    start_date: date,
    end_date: date,
) -> ObservedNavReturn:
    """Compute a published NAV ratio without accepting or reapplying TER."""
    first = _decimal(start_nav, "start_nav")
    last = _decimal(end_nav, "end_nav")
    if first <= 0 or last <= 0:
        raise MfReturnError("NAV values must be positive")
    if end_date <= start_date:
        raise MfReturnError("end_date must follow start_date")
    return ObservedNavReturn(
        start_date=start_date, end_date=end_date, start_nav=first, end_nav=last,
        nav_change_percent=(last / first - Decimal(1)) * Decimal(100),
    )


def forward_net_projection(
    gross_annual_return_percent: Decimal | str | int,
    ter_annual_percent: Decimal | str | int,
    *,
    years: int = 1,
) -> ForwardNetProjection:
    """Apply TER only to an explicitly forward-looking gross-return assumption."""
    gross = _decimal(gross_annual_return_percent, "gross annual return")
    ter = _decimal(ter_annual_percent, "annual TER")
    if gross <= -100:
        raise MfReturnError("gross return must exceed -100 percent")
    if not Decimal(0) <= ter < Decimal(100):
        raise MfReturnError("TER must be in [0, 100) percent")
    if isinstance(years, bool) or years < 1:
        raise MfReturnError("years must be a positive integer")
    gross_factor = (Decimal(1) + gross / Decimal(100)) ** years
    fee_factor = (Decimal(1) - ter / Decimal(100)) ** years
    projected = (gross_factor * fee_factor - Decimal(1)) * Decimal(100)
    return ForwardNetProjection(gross, ter, years, projected)


__all__ = ["ForwardNetProjection", "MfReturnError", "ObservedNavReturn",
           "forward_net_projection", "observed_nav_change"]
