"""Typed units for money, prices and quantities (FB-0042, FB-0586 decimal policy).

Amounts are ``Decimal`` so that cents and paise never pick up binary floating-point error. Floats are accepted
only when finite, and are converted through their shortest decimal representation. Arithmetic refuses to mix
units: a ``Money`` cannot be added to a ``Price`` or a ``Quantity``, and two ``Money`` values in different
currencies cannot be combined without an explicit, dated FX conversion elsewhere in the system.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

_CURRENCY = re.compile(r"^[A-Z]{3}$")


class UnitError(ValueError):
    """Raised for a non-finite value, a malformed currency code or a unit mismatch."""


def to_decimal(value: object) -> Decimal:
    if isinstance(value, bool):
        raise UnitError("booleans are not amounts")
    if isinstance(value, Decimal):
        d = value
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise UnitError("non-finite float")
        d = Decimal(repr(value))
    elif isinstance(value, int):
        d = Decimal(value)
    elif isinstance(value, str):
        try:
            d = Decimal(value)
        except InvalidOperation as exc:
            raise UnitError(f"not a number: {value!r}") from exc
    else:
        raise UnitError(f"unsupported amount type: {type(value).__name__}")
    if not d.is_finite():
        raise UnitError("non-finite amount")
    return d


def _check_currency(code: str) -> str:
    if not isinstance(code, str) or not _CURRENCY.match(code):
        raise UnitError(f"currency must be an ISO-4217 style three-letter upper-case code: {code!r}")
    return code


@dataclass(frozen=True)
class Money:
    amount: Decimal
    currency: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "amount", to_decimal(self.amount))
        _check_currency(self.currency)

    @classmethod
    def of(cls, amount: object, currency: str) -> Money:
        return cls(to_decimal(amount), currency)

    def _same(self, other: object) -> Money:
        if not isinstance(other, Money):
            raise UnitError(f"cannot combine Money with {type(other).__name__}")
        if other.currency != self.currency:
            raise UnitError(f"currency mismatch: {self.currency} vs {other.currency}")
        return other

    def __add__(self, other: object) -> Money:
        o = self._same(other)
        return Money(self.amount + o.amount, self.currency)

    def __sub__(self, other: object) -> Money:
        o = self._same(other)
        return Money(self.amount - o.amount, self.currency)

    def __neg__(self) -> Money:
        return Money(-self.amount, self.currency)

    def __mul__(self, scalar: object) -> Money:
        if isinstance(scalar, Money) or isinstance(scalar, bool):
            raise UnitError("Money may only be scaled by a plain number")
        return Money(self.amount * to_decimal(scalar), self.currency)

    __rmul__ = __mul__


@dataclass(frozen=True)
class Quantity:
    units: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "units", to_decimal(self.units))

    def __add__(self, other: object) -> Quantity:
        if not isinstance(other, Quantity):
            raise UnitError(f"cannot add Quantity to {type(other).__name__}")
        return Quantity(self.units + other.units)

    def __neg__(self) -> Quantity:
        return Quantity(-self.units)


@dataclass(frozen=True)
class Price:
    value: Decimal
    currency: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", to_decimal(self.value))
        _check_currency(self.currency)
        if self.value <= 0:
            raise UnitError("a traded price must be strictly positive")

    def notional(self, quantity: Quantity, multiplier: object = 1) -> Money:
        """Position value = price x quantity x contract multiplier, in the price's currency."""
        if not isinstance(quantity, Quantity):
            raise UnitError("notional needs a Quantity")
        return Money(self.value * quantity.units * to_decimal(multiplier), self.currency)
