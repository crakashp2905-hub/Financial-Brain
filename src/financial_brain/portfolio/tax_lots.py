"""Effective-dated India tax classification and capital-gain lots (FB-0907, FB-0908, FB-0909, FB-1208).

Rates and holding periods are *data* supplied with an effective date and a source; no rate is built
in. Estimates are kept apart from trading fees. Flows are recorded separately from returns.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal


class TaxError(ValueError):
    pass


@dataclass(frozen=True)
class TaxRule:
    effective_from: date
    tax_class: str             # EQUITY_ORIENTED, DEBT_SPECIFIED, GOLD_INTL, OTHER
    long_term_days: int
    short_rate: Decimal
    long_rate: Decimal
    long_exemption: Decimal
    source: str


@dataclass
class TaxBook:
    rules: list[TaxRule] = field(default_factory=list)
    classes: dict[str, list[tuple[date, str]]] = field(default_factory=dict)

    def classify(self, instrument: str, on: date) -> str:
        hist = sorted(x for x in self.classes.get(instrument, []) if x[0] <= on)
        if not hist:
            raise TaxError(f"no tax class for {instrument} on {on}")
        return hist[-1][1]

    def rule(self, tax_class: str, on: date) -> TaxRule:
        hits = sorted((r for r in self.rules if r.tax_class == tax_class and r.effective_from <= on),
                      key=lambda r: r.effective_from)
        if not hits:
            raise TaxError(f"no tax rule for {tax_class} on {on}")
        return hits[-1]


@dataclass
class Lot:
    instrument: str
    acquired: date
    units: Decimal
    cost: Decimal


@dataclass(frozen=True)
class Realized:
    instrument: str
    acquired: date
    sold: date
    units: Decimal
    gain: Decimal
    term: str
    est_tax: Decimal
    report_year: str


def fy(d: date) -> str:
    start = d.year if d.month >= 4 else d.year - 1
    return f"FY{start}-{str(start + 1)[2:]}"


def sell_fifo(lots: list[Lot], instrument: str, units: Decimal, price: Decimal, sold: date,
              book: TaxBook) -> list[Realized]:
    """FIFO disposal; each slice classified by its own holding period and dated rule."""
    held = [lot for lot in lots if lot.instrument == instrument]
    if units > sum((lot.units for lot in held), Decimal(0)):
        raise TaxError("selling more than held")
    rule = book.rule(book.classify(instrument, sold), sold)
    out, left = [], units
    for lot in held:
        if left == 0:
            break
        take = min(left, lot.units)
        basis = lot.cost * take / lot.units
        gain = take * price - basis
        long = (sold - lot.acquired).days >= rule.long_term_days
        rate = rule.long_rate if long else rule.short_rate
        out.append(Realized(instrument, lot.acquired, sold, take, gain, "LONG" if long else "SHORT",
                            max(gain, Decimal(0)) * rate, fy(sold)))
        lot.cost -= basis
        lot.units -= take
        left -= take
    lots[:] = [lot for lot in lots if lot.units > 0]
    return out


@dataclass
class Flows:
    """External contributions/withdrawals kept apart from returns (FB-0907)."""

    items: list[tuple[date, Decimal]] = field(default_factory=list)

    def add(self, on: date, amount: Decimal) -> None:
        self.items.append((on, amount))

    def net(self) -> Decimal:
        return sum((a for _, a in self.items), Decimal(0))


def desk_contribution(desk_pnl: dict[str, float], start_nav: float) -> dict[str, float]:
    """Each desk's contribution to portfolio return, capital-weighted by the shared start NAV (FB-1208)."""
    if start_nav <= 0:
        raise TaxError("start NAV must be positive")
    return {k: v / start_nav for k, v in desk_pnl.items()}
