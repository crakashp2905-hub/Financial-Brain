"""Financial unit types per asset class (FB-0103).

Equities and ETFs trade in whole exchange units, options in lots of a contract's lot size, mutual funds in
fractional units at a published NAV. Each has its own type so that unlike things cannot be multiplied: a
lot count times a NAV, or fund units times an option premium, raises instead of producing a number.

Money, decimal conversion and the currency check come from ``kernel.units``.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal

from financial_brain.kernel.units import Money, UnitError, to_decimal

INR = "INR"
#: Decimal places AMCs commonly allot units to. A scheme's own figure belongs in its terms.
DEFAULT_UNIT_PLACES = 3
DEFAULT_NAV_PLACES = 4


def inr(amount: object) -> Money:
    return Money.of(amount, INR)


def _whole(value: object, what: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise UnitError(f"{what} must be a whole number, got {value!r}")
    if value < minimum:
        raise UnitError(f"{what} must be at least {minimum}, got {value}")
    return value


def _places(value: Decimal, places: int, what: str) -> Decimal:
    q = Decimal(1).scaleb(-places)
    if value != value.quantize(q):
        raise UnitError(f"{what} {value} has more than {places} decimal places")
    return value.quantize(q)


@dataclass(frozen=True)
class WholeUnits:
    """Shares or ETF units. Always a non-negative magnitude; direction lives on the order."""

    units: int

    def __post_init__(self) -> None:
        _whole(self.units, "exchange units")


@dataclass(frozen=True)
class OptionLots:
    """A number of lots of one contract. ``lot_size`` is the contract's, effective on the trade date."""

    lots: int
    lot_size: int

    def __post_init__(self) -> None:
        _whole(self.lots, "lots")
        _whole(self.lot_size, "lot size", minimum=1)

    @property
    def units(self) -> int:
        return self.lots * self.lot_size


@dataclass(frozen=True)
class NavPerUnit:
    """A published net asset value per fund unit, in rupees."""

    value: Decimal
    places: int = DEFAULT_NAV_PLACES

    def __post_init__(self) -> None:
        v = to_decimal(self.value)
        if v <= 0:
            raise UnitError("a NAV must be strictly positive")
        object.__setattr__(self, "value", _places(v, self.places, "NAV"))


@dataclass(frozen=True)
class MfUnits:
    """Fractional mutual-fund units, held to the scheme's allotment precision."""

    units: Decimal
    places: int = DEFAULT_UNIT_PLACES

    def __post_init__(self) -> None:
        v = to_decimal(self.units)
        if v < 0:
            raise UnitError("fund units cannot be negative")
        object.__setattr__(self, "units", _places(v, self.places, "fund units"))

    @classmethod
    def allot(cls, amount: Money, nav: NavPerUnit, places: int = DEFAULT_UNIT_PLACES) -> MfUnits:
        """Units for a rupee amount at a NAV, truncated (never rounded up) to the allotment precision.

        Truncation is the conservative convention; a scheme that rounds differently needs its own rule.
        """
        if not isinstance(amount, Money) or amount.currency != INR:
            raise UnitError("allotment needs a rupee amount")
        if not isinstance(nav, NavPerUnit):
            raise UnitError("allotment needs a NavPerUnit")
        if amount.amount < 0:
            raise UnitError("cannot allot units for a negative amount")
        q = Decimal(1).scaleb(-places)
        return cls((amount.amount / nav.value).quantize(q, rounding=ROUND_DOWN), places)


@dataclass(frozen=True)
class PremiumPerUnit:
    """An option premium per unit of the underlying, in rupees. Zero is allowed (a worthless bid)."""

    value: Decimal

    def __post_init__(self) -> None:
        v = to_decimal(self.value)
        if v < 0:
            raise UnitError("a premium cannot be negative")
        object.__setattr__(self, "value", v)


@dataclass(frozen=True)
class SharePrice:
    """An exchange price per share or ETF unit, in rupees."""

    value: Decimal

    def __post_init__(self) -> None:
        v = to_decimal(self.value)
        if v <= 0:
            raise UnitError("a traded price must be strictly positive")
        object.__setattr__(self, "value", v)

    def on_tick(self, tick: object) -> bool:
        t = to_decimal(tick)
        if t <= 0:
            raise UnitError("tick must be positive")
        return self.value % t == 0


@dataclass(frozen=True)
class BasisPoints:
    value: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", to_decimal(self.value))

    @property
    def fraction(self) -> Decimal:
        return self.value / Decimal(10_000)

    def of(self, amount: Money) -> Money:
        if not isinstance(amount, Money):
            raise UnitError("basis points apply to Money")
        return Money(amount.amount * self.fraction, amount.currency)


#: Which quantity and price types belong to which asset class.
PAIRS: dict[str, tuple[type, type]] = {
    "EQUITY": (WholeUnits, SharePrice),
    "ETF": (WholeUnits, SharePrice),
    "FUTURE": (OptionLots, SharePrice),
    "OPTION": (OptionLots, PremiumPerUnit),
    "MUTUAL_FUND": (MfUnits, NavPerUnit),
}


def notional(asset_class: str, quantity: object, price: object) -> Money:
    """Rupee value of a quantity at a price, refusing any pairing that is not the asset class's own."""
    key = getattr(asset_class, "value", asset_class)
    if key not in PAIRS:
        raise UnitError(f"{key!r} has no tradable unit")
    q_type, p_type = PAIRS[key]
    if type(quantity) is not q_type or type(price) is not p_type:
        raise UnitError(f"{key} needs {q_type.__name__} x {p_type.__name__}, got "
                        f"{type(quantity).__name__} x {type(price).__name__}")
    return inr(to_decimal(quantity.units) * price.value)  # type: ignore[attr-defined]
