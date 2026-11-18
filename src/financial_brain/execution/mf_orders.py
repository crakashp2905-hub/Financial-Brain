"""Paper mutual-fund order lifecycle under NAV mechanics (checklist section 7).

An MF signal creates a request, never a fill at a displayed NAV. The applicable NAV date follows from
when the request was accepted **and** when funds were available, against a cut-off rule the caller
supplies (no universal cut-off is built in). Units are allotted only once that day's NAV has actually
been published; until then the order is ``NAV_PENDING``. Redemptions pick lots FIFO, charge exit loads
per lot by holding age, and proceeds pass through settlement states before they can be reused.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import ROUND_DOWN, Decimal
from enum import Enum


class MfOrderError(ValueError):
    pass


class OrderState(str, Enum):
    REQUESTED = "REQUESTED"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    NAV_PENDING = "NAV_PENDING"
    ALLOTTED = "ALLOTTED"


class CashState(str, Enum):
    REDEMPTION_PENDING = "REDEMPTION_PENDING"
    VALUE_CONFIRMED = "VALUE_CONFIRMED"
    PAYOUT_PENDING = "PAYOUT_PENDING"
    CASH_AVAILABLE = "CASH_AVAILABLE"


_CASH_NEXT = {
    CashState.REDEMPTION_PENDING: CashState.VALUE_CONFIRMED,
    CashState.VALUE_CONFIRMED: CashState.PAYOUT_PENDING,
    CashState.PAYOUT_PENDING: CashState.CASH_AVAILABLE,
}


def _aware(ts: datetime, what: str) -> None:
    if ts.tzinfo is None or ts.utcoffset() is None:
        raise MfOrderError(f"{what} must be timezone-aware")


def eligible_nav_date(accepted_at: datetime, funds_available_at: datetime | None, *, cutoff: time,
                      is_scheme_day: Callable[[date], bool], tz) -> date | None:
    """First scheme business day whose cut-off both acceptance and funds beat (FB-0705).

    Returns ``None`` when funds have not been realised: the NAV date is then unknown, not assumed.
    The request must be strictly before the cut-off to count for that day.
    """
    _aware(accepted_at, "accepted_at")
    if funds_available_at is None:
        return None
    _aware(funds_available_at, "funds_available_at")
    latest = max(accepted_at, funds_available_at).astimezone(tz)
    d = latest.date()
    if latest.timetz().replace(tzinfo=None) >= cutoff or not is_scheme_day(d):
        d = date.fromordinal(d.toordinal() + 1)
    for _ in range(30):
        if is_scheme_day(d):
            return d
        d = date.fromordinal(d.toordinal() + 1)
    raise MfOrderError("no scheme business day within 30 days")


@dataclass(frozen=True)
class PublishedNav:
    nav_date: date
    nav: Decimal
    published_at: datetime


@dataclass
class Lot:
    bought_on: date
    units: Decimal
    cost: Decimal


@dataclass
class Subscription:
    order_id: str
    scheme_uid: str
    amount: Decimal
    requested_at: datetime
    state: OrderState = OrderState.REQUESTED
    nav_date: date | None = None
    units: Decimal | None = None

    def accept(self, nav_date: date | None) -> None:
        if self.state is not OrderState.REQUESTED:
            raise MfOrderError("only a requested order can be accepted")
        self.state = OrderState.NAV_PENDING
        self.nav_date = nav_date

    def allot(self, nav: PublishedNav, now: datetime, places: int = 3) -> Lot:
        """Allot units at the eligible day's NAV, only once it is published (FB-0706)."""
        _aware(now, "now")
        if self.state is not OrderState.NAV_PENDING or self.nav_date is None:
            raise MfOrderError("order has no known eligible NAV date")
        if nav.nav_date != self.nav_date:
            raise MfOrderError("a different day's NAV cannot be chosen (FB-0705)")
        if nav.published_at > now:
            raise MfOrderError("NAV not yet published")
        q = Decimal(1).scaleb(-places)
        self.units = (self.amount / nav.nav).quantize(q, rounding=ROUND_DOWN)
        self.state = OrderState.ALLOTTED
        return Lot(self.nav_date, self.units, self.amount)


@dataclass(frozen=True)
class LoadRule:
    """Exit load as a fraction when held for fewer than ``days`` (effective-dated by the caller)."""

    days: int
    fraction: Decimal


@dataclass
class Redemption:
    units: Decimal
    gross: Decimal
    load: Decimal
    state: CashState = CashState.REDEMPTION_PENDING

    @property
    def net(self) -> Decimal:
        return self.gross - self.load

    def advance(self) -> None:
        if self.state not in _CASH_NEXT:
            raise MfOrderError("proceeds already available")
        self.state = _CASH_NEXT[self.state]

    @property
    def spendable(self) -> Decimal:
        """Proceeds may be redeployed only once cash is available (FB-0709)."""
        return self.net if self.state is CashState.CASH_AVAILABLE else Decimal(0)


@dataclass
class Holding:
    lots: list[Lot] = field(default_factory=list)

    @property
    def units(self) -> Decimal:
        return sum((lot.units for lot in self.lots), Decimal(0))

    def redeem(self, units: Decimal, nav: PublishedNav, now: datetime,
               load: LoadRule | None = None) -> Redemption:
        """FIFO redemption with per-lot exit load (FB-0707, FB-0708, FB-0714)."""
        _aware(now, "now")
        if nav.published_at > now:
            raise MfOrderError("NAV not yet published")
        if units <= 0 or units > self.units:
            raise MfOrderError("cannot redeem more units than held")
        left, gross, charge = units, Decimal(0), Decimal(0)
        while left > 0:
            lot = self.lots[0]
            take = min(left, lot.units)
            value = take * nav.nav
            gross += value
            if load and (nav.nav_date - lot.bought_on).days < load.days:
                charge += value * load.fraction
            lot.cost -= lot.cost * take / lot.units
            lot.units -= take
            left -= take
            if lot.units == 0:
                self.lots.pop(0)
        return Redemption(units, gross, charge)
