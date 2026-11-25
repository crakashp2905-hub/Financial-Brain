"""Corporate actions applied to a holding (FB-0108, and the holdings half of FB-0212 and FB-0316).

A split, a bonus, an ISIN change and a scheme merger change the unit count or the identity. None of them
changes what the holder paid, so the total cost basis is carried through every one. A distribution is an
economic flow: paid out it is cash, reinvested it buys units and adds to the basis. None of these is a
price move, and none may be read as a loss.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_DOWN, Decimal

from financial_brain.instruments.master import InstrumentError, InstrumentMaster, Succession
from financial_brain.instruments.units import DEFAULT_UNIT_PLACES, INR, NavPerUnit, inr
from financial_brain.kernel.units import Money, to_decimal


@dataclass(frozen=True)
class Holding:
    uid: str
    units: Decimal
    cost: Money                    # total cost basis, not per unit

    def __post_init__(self) -> None:
        u = to_decimal(self.units)
        if u < 0:
            raise InstrumentError("a holding cannot have negative units")
        object.__setattr__(self, "units", u)
        if not isinstance(self.cost, Money) or self.cost.currency != INR or self.cost.amount < 0:
            raise InstrumentError("cost basis is a non-negative rupee amount")


@dataclass(frozen=True)
class Split:
    """A split or bonus: ``ratio`` new units per old unit (a 1:1 bonus is 2, a 10-for-1 split is 10)."""

    uid: str
    ex_date: date
    ratio: Decimal

    def __post_init__(self) -> None:
        r = to_decimal(self.ratio)
        if r <= 0:
            raise InstrumentError("split ratio must be positive")
        object.__setattr__(self, "ratio", r)


@dataclass(frozen=True)
class Distribution:
    """A dividend or IDCW of ``per_unit`` rupees. With ``reinvest_nav`` it is reinvested at that NAV."""

    uid: str
    ex_date: date
    per_unit: Money
    reinvest_nav: NavPerUnit | None = None


@dataclass(frozen=True)
class Outcome:
    holding: Holding
    cash: Money                    # cash received by the holder
    fraction: Decimal              # fractional entitlement a whole-unit asset could not hold
    event: str


def _scaled(h: Holding, uid: str, ratio: Decimal, whole_units: bool, event: str) -> Outcome:
    units = h.units * ratio
    fraction = Decimal(0)
    if whole_units:
        kept = units.to_integral_value(rounding=ROUND_DOWN)
        fraction, units = units - kept, kept
    return Outcome(Holding(uid, units, h.cost), inr(0), fraction, event)


def apply_split(h: Holding, s: Split, *, whole_units: bool) -> Outcome:
    """Units scale by the ratio and the basis is unchanged.

    For a whole-unit asset the fractional entitlement is reported, not silently dropped or cashed: how
    it is settled is the issuer's decision and is booked when known.
    """
    if s.uid != h.uid:
        raise InstrumentError("split is for a different instrument")
    return _scaled(h, h.uid, s.ratio, whole_units, "SPLIT")


def apply_succession(h: Holding, s: Succession, *, whole_units: bool) -> Outcome:
    """The holding continues under the new uid at the succession ratio, with its basis intact."""
    if s.old_uid != h.uid:
        raise InstrumentError("succession is from a different instrument")
    return _scaled(h, s.new_uid, s.ratio, whole_units, s.kind)


def apply_distribution(h: Holding, d: Distribution, *, places: int = DEFAULT_UNIT_PLACES) -> Outcome:
    if d.uid != h.uid:
        raise InstrumentError("distribution is for a different instrument")
    if d.per_unit.currency != INR or d.per_unit.amount < 0:
        raise InstrumentError("a distribution is a non-negative rupee amount per unit")
    cash = inr(h.units * d.per_unit.amount)
    if d.reinvest_nav is None:
        return Outcome(h, cash, Decimal(0), "DISTRIBUTION_PAID")
    q = Decimal(1).scaleb(-places)
    bought = (cash.amount / d.reinvest_nav.value).quantize(q, rounding=ROUND_DOWN)
    return Outcome(Holding(h.uid, h.units + bought, h.cost + cash), inr(0), Decimal(0),
                   "DISTRIBUTION_REINVESTED")


def roll_forward(h: Holding, master: InstrumentMaster, *, after: date, through: date,
                 whole_units: bool, known_at: datetime | None = None) -> tuple[Holding, list[Outcome]]:
    """Carry a holding through every succession effective in ``(after, through]``."""
    steps = []
    for s in master.successions(h.uid, after=after, through=through, known_at=known_at):
        out = apply_succession(h, s, whole_units=whole_units)
        steps.append(out)
        h = out.holding
    return h, steps
