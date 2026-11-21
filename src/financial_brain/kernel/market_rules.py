"""Order-ticket validation (FB-0088) and session gap reports (FB-0068).

* ``validate_ticket`` returns every violated rule; an empty list means the ticket is valid.
* ``session_gaps`` separates expected-holiday absences from unexplained failures, so reports never call a holiday a
  failure and never hide a real missing session.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class TicketRules:
    lot_size: int
    min_notional: Decimal
    max_freeze_quantity: int


def validate_ticket(quantity: int, price: Decimal, rules: TicketRules) -> list[str]:
    reasons: list[str] = []
    if quantity <= 0:
        reasons.append("quantity must be positive")
    if rules.lot_size <= 0:
        reasons.append("lot size must be positive")
    elif quantity % rules.lot_size != 0:
        reasons.append(f"quantity not a multiple of lot size {rules.lot_size}")
    if quantity > rules.max_freeze_quantity:
        reasons.append(f"quantity exceeds freeze limit {rules.max_freeze_quantity}")
    if price <= 0:
        reasons.append("price must be positive")
    elif Decimal(quantity) * price < rules.min_notional:
        reasons.append(f"notional below minimum {rules.min_notional}")
    return reasons


@dataclass(frozen=True)
class GapReport:
    holiday_gaps: list[date] = field(default_factory=list)
    unexplained_gaps: list[date] = field(default_factory=list)


def session_gaps(expected: list[date], observed: set[date], holidays: set[date]) -> GapReport:
    """Expected trading days with no data. Holidays are excluded from failures."""
    report = GapReport()
    for day in sorted(set(expected)):
        if day in observed:
            continue
        if day in holidays:
            report.holiday_gaps.append(day)
        else:
            report.unexplained_gaps.append(day)
    return report
