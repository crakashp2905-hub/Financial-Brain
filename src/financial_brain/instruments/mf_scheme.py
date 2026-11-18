"""Effective-dated mutual-fund scheme master (checklist section 3).

Keyed by (scheme code, plan, option, ISIN). Each attribute set is a dated version with provenance,
so a reclassification or TER change is visible on its own date and old dates keep old values.
Wound-up, merged and closed schemes stay in the universe for the dates they existed
(survivorship-aware). Exit loads are schedules evaluated per purchase lot.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum


class SchemeError(ValueError):
    pass


class Plan(str, Enum):
    DIRECT = "DIRECT"
    REGULAR = "REGULAR"


class Option(str, Enum):
    GROWTH = "GROWTH"
    IDCW_PAYOUT = "IDCW_PAYOUT"
    IDCW_REINVEST = "IDCW_REINVEST"


class Structure(str, Enum):
    OPEN_ENDED = "OPEN_ENDED"
    CLOSE_ENDED = "CLOSE_ENDED"
    INTERVAL = "INTERVAL"


class AssetType(str, Enum):
    EQUITY = "EQUITY"
    DEBT = "DEBT"
    HYBRID = "HYBRID"
    COMMODITY = "COMMODITY"
    FOF = "FOF"
    OTHER = "OTHER"


@dataclass(frozen=True)
class SchemeKey:
    code: str
    plan: Plan
    option: Option
    isin: str


@dataclass(frozen=True)
class SchemeVersion:
    effective_from: date
    source: str
    category: str
    asset_type: AssetType
    structure: Structure
    passive: bool
    fund_house: str
    benchmark_tri: str | None = None
    riskometer: str | None = None
    ter: Decimal | None = None
    aum_cr: Decimal | None = None
    status: str = "ACTIVE"          # ACTIVE, CLOSED_FOR_PURCHASE, MERGED, WOUND_UP, SUSPENDED


@dataclass(frozen=True)
class LoadStep:
    """Exit load ``fraction`` applies while held for fewer than ``days``."""

    days: int
    fraction: Decimal


@dataclass(frozen=True)
class LoadSchedule:
    effective_from: date
    steps: tuple[LoadStep, ...]

    def load_for(self, held_days: int) -> Decimal:
        for s in sorted(self.steps, key=lambda s: s.days):
            if held_days < s.days:
                return s.fraction
        return Decimal(0)


@dataclass
class SchemeMaster:
    versions: dict[SchemeKey, list[SchemeVersion]] = field(default_factory=dict)
    loads: dict[SchemeKey, list[LoadSchedule]] = field(default_factory=dict)

    def add(self, key: SchemeKey, v: SchemeVersion) -> None:
        vs = self.versions.setdefault(key, [])
        if any(x.effective_from == v.effective_from for x in vs):
            raise SchemeError("a version already starts on that date")
        vs.append(v)
        vs.sort(key=lambda x: x.effective_from)

    def add_loads(self, key: SchemeKey, s: LoadSchedule) -> None:
        self.loads.setdefault(key, []).append(s)
        self.loads[key].sort(key=lambda x: x.effective_from)

    def as_of(self, key: SchemeKey, on: date) -> SchemeVersion | None:
        vs = [v for v in self.versions.get(key, []) if v.effective_from <= on]
        return vs[-1] if vs else None

    def universe(self, on: date) -> list[SchemeKey]:
        """Every scheme that existed on ``on``, including ones later merged or wound up."""
        out = []
        for k in self.versions:
            v = self.as_of(k, on)
            if v is not None and v.status not in ("WOUND_UP", "MERGED"):
                out.append(k)
        return sorted(out, key=lambda k: (k.code, k.plan.value, k.option.value))

    def exit_load(self, key: SchemeKey, bought_on: date, redeemed_on: date) -> Decimal:
        """Load for one lot: schedule in force at purchase, applied to that lot's holding age."""
        ss = [s for s in self.loads.get(key, []) if s.effective_from <= bought_on]
        if not ss:
            raise SchemeError("no exit-load schedule known for that purchase date")
        return ss[-1].load_for((redeemed_on - bought_on).days)
