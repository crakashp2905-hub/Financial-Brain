"""Multi-asset, multi-desk double-entry paper journal (checklist section 9).

Every economic event posts balanced lines (sum of debits equals sum of credits) to named accounts.
Cash is split into AVAILABLE, RESERVED, UNSETTLED and MF_PENDING so that unsettled sale proceeds or a
pending fund subscription can never be spent twice. Positions are keyed by (desk, instrument); desk
views are attribution over the same journal, so desk NAVs sum to the consolidated NAV exactly.
Valuation needs a mark per position; a missing or stale mark is reported UNPRICED, never as zero.
Exposure is also aggregated by economic underlying, so NIFTY stock, ETF, fund and option holdings
add up instead of looking diversified.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal


class LedgerError(ValueError):
    pass


CASH_BUCKETS = ("AVAILABLE", "RESERVED", "UNSETTLED", "MF_PENDING")


@dataclass(frozen=True)
class Line:
    account: str
    debit: Decimal = Decimal(0)
    credit: Decimal = Decimal(0)


@dataclass(frozen=True)
class Entry:
    key: str
    at: datetime
    desk: str
    memo: str
    lines: tuple[Line, ...]


@dataclass(frozen=True)
class Mark:
    price: Decimal
    at: datetime


@dataclass
class Journal:
    entries: list[Entry] = field(default_factory=list)
    units: dict[tuple[str, str], Decimal] = field(default_factory=lambda: defaultdict(Decimal))
    underlying: dict[str, str] = field(default_factory=dict)

    # ---------------------------------------------------------------- posting

    def post(self, key: str, at: datetime, desk: str, memo: str, lines: list[Line]) -> bool:
        """Post a balanced entry once. Returns False if the key was already posted (idempotent)."""
        if at.tzinfo is None:
            raise LedgerError("timestamps must be timezone-aware")
        if any(e.key == key for e in self.entries):
            return False
        dr = sum((ln.debit for ln in lines), Decimal(0))
        cr = sum((ln.credit for ln in lines), Decimal(0))
        if dr != cr or dr == 0:
            raise LedgerError(f"unbalanced entry {key}: debit {dr} credit {cr}")
        if any(ln.debit < 0 or ln.credit < 0 for ln in lines):
            raise LedgerError("negative amounts are not allowed; swap the side")
        self.entries.append(Entry(key, at, desk, memo, tuple(lines)))
        return True

    def balance(self, account: str, desk: str | None = None) -> Decimal:
        """Debit-positive balance of an account, optionally for one desk."""
        total = Decimal(0)
        for e in self.entries:
            if desk is not None and e.desk != desk:
                continue
            for ln in e.lines:
                if ln.account == account:
                    total += ln.debit - ln.credit
        return total

    def cash(self, bucket: str, desk: str | None = None) -> Decimal:
        if bucket not in CASH_BUCKETS:
            raise LedgerError(f"unknown cash bucket {bucket}")
        return self.balance(f"CASH:{bucket}", desk)

    # ---------------------------------------------------------------- economic events

    def contribute(self, key: str, at: datetime, desk: str, amount: Decimal) -> None:
        """External capital in; kept apart from returns (FB-0907)."""
        self.post(key, at, desk, "contribution", [Line("CASH:AVAILABLE", debit=amount),
                                                  Line("EQUITY:CONTRIBUTIONS", credit=amount)])

    def buy(self, key: str, at: datetime, desk: str, instrument: str, units: Decimal,
            price: Decimal, fees: Decimal = Decimal(0)) -> None:
        cost = units * price
        if cost + fees > self.cash("AVAILABLE", desk):
            raise LedgerError("insufficient available cash (pending or unsettled cash is not spendable)")
        lines = [Line(f"POS:{instrument}", debit=cost), Line("CASH:AVAILABLE", credit=cost + fees)]
        if fees:
            lines.append(Line("EXPENSE:FEES", debit=fees))
        if self.post(key, at, desk, f"buy {instrument}", lines):
            self.units[(desk, instrument)] += units

    def sell(self, key: str, at: datetime, desk: str, instrument: str, units: Decimal,
             price: Decimal, fees: Decimal = Decimal(0)) -> None:
        """Sale proceeds land in UNSETTLED; they are not spendable until settle() (FB-0911)."""
        held = self.units[(desk, instrument)]
        if units <= 0 or units > held:
            raise LedgerError("cannot sell more than held")
        book = self.balance(f"POS:{instrument}", desk) * units / held
        proceeds = units * price
        pnl = proceeds - book
        lines = [Line("CASH:UNSETTLED", debit=proceeds - fees), Line(f"POS:{instrument}", credit=book)]
        if fees:
            lines.append(Line("EXPENSE:FEES", debit=fees))
        if pnl >= 0:
            lines.append(Line("INCOME:REALIZED", credit=pnl))
        else:
            lines.append(Line("INCOME:REALIZED", debit=-pnl))
        if self.post(key, at, desk, f"sell {instrument}", lines):
            self.units[(desk, instrument)] -= units

    def settle(self, key: str, at: datetime, desk: str, amount: Decimal) -> None:
        if amount > self.cash("UNSETTLED", desk):
            raise LedgerError("cannot settle more than is unsettled")
        self.post(key, at, desk, "settlement", [Line("CASH:AVAILABLE", debit=amount),
                                                Line("CASH:UNSETTLED", credit=amount)])

    def mf_request(self, key: str, at: datetime, desk: str, amount: Decimal) -> None:
        """Subscription paid but not yet allotted: cash leaves AVAILABLE into MF_PENDING."""
        if amount > self.cash("AVAILABLE", desk):
            raise LedgerError("insufficient available cash")
        self.post(key, at, desk, "mf subscription pending", [Line("CASH:MF_PENDING", debit=amount),
                                                              Line("CASH:AVAILABLE", credit=amount)])

    def mf_allot(self, key: str, at: datetime, desk: str, scheme: str, amount: Decimal,
                 units: Decimal) -> None:
        if amount > self.cash("MF_PENDING", desk):
            raise LedgerError("allotment exceeds pending subscription")
        if self.post(key, at, desk, f"allot {scheme}", [Line(f"POS:{scheme}", debit=amount),
                                                        Line("CASH:MF_PENDING", credit=amount)]):
            self.units[(desk, scheme)] += units

    # ---------------------------------------------------------------- valuation

    def nav(self, marks: dict[str, Mark], now: datetime, max_age, desk: str | None = None) -> dict:
        """Cash plus marked positions. Stale or missing marks are listed, never valued at zero."""
        cash = sum((self.cash(b, desk) for b in CASH_BUCKETS), Decimal(0))
        value, unpriced = Decimal(0), []
        for (d, inst), u in self.units.items():
            if u == 0 or (desk is not None and d != desk):
                continue
            m = marks.get(inst)
            if m is None or now - m.at > max_age:
                unpriced.append(inst)
                continue
            value += u * m.price
        return {"cash": cash, "positions": value, "nav": cash + value if not unpriced else None,
                "unpriced": sorted(set(unpriced))}

    def check(self) -> None:
        """Journal invariant: total debits equal total credits (FB-0917)."""
        dr = sum((ln.debit for e in self.entries for ln in e.lines), Decimal(0))
        cr = sum((ln.credit for e in self.entries for ln in e.lines), Decimal(0))
        if dr != cr:
            raise LedgerError("journal out of balance")

    # ---------------------------------------------------------------- exposure (FB-0914)

    def exposure_by_underlying(self, marks: dict[str, Mark], deltas: dict[str, Decimal]
                               | None = None) -> dict[str, Decimal]:
        """Rupee exposure summed by economic underlying across desks and wrappers."""
        out: dict[str, Decimal] = defaultdict(Decimal)
        for (_, inst), u in self.units.items():
            if u == 0 or inst not in marks:
                continue
            delta = (deltas or {}).get(inst, Decimal(1))
            out[self.underlying.get(inst, inst)] += u * marks[inst].price * delta
        return dict(out)
