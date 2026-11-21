"""Symbol lineage (FB-0089), prediction fingerprints (FB-0048), index membership (FB-0090) and split adjustment
reconciliation (FB-0091).

* ``SymbolLineage``: a rename keeps the instrument id; a symbol that is taken by an active listing cannot be reused by
  an unlinked instrument. A delisted instrument's history is never merged with a new unrelated listing.
* ``PredictionRecord`` refuses a prediction unless it is linked to a model fingerprint and a data fingerprint.
* ``membership_on`` returns the latest constituent snapshot on or before a date, flagged when none exists or when it is
  too stale. It never falls back to present-day membership.
* ``adjust_for_split`` changes quantity and average cost; notional is preserved and ``reconciles`` checks it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class Listing:
    instrument_id: str
    symbol: str
    start: date
    end: date | None = None
    predecessor: str | None = None  # instrument id this listing continues (rename link)


@dataclass
class SymbolLineage:
    listings: list[Listing] = field(default_factory=list)

    def register(self, listing: Listing) -> None:
        if listing.end is not None and listing.end < listing.start:
            raise ValueError("listing ends before it starts")
        for existing in self.listings:
            overlaps = existing.end is None or listing.start <= existing.end
            same_symbol = existing.symbol == listing.symbol
            if same_symbol and overlaps and existing.instrument_id != listing.instrument_id:
                if listing.predecessor != existing.instrument_id:
                    raise ValueError(
                        f"symbol {listing.symbol} is held by {existing.instrument_id}; unlinked reuse refused"
                    )
        self.listings.append(listing)

    def resolve(self, symbol: str, on: date) -> str | None:
        for listing in self.listings:
            active = listing.start <= on and (listing.end is None or on <= listing.end)
            if listing.symbol == symbol and active:
                return listing.instrument_id
        return None


@dataclass(frozen=True)
class PredictionRecord:
    prediction: Decimal
    model_fingerprint: str
    data_fingerprint: str

    def __post_init__(self) -> None:
        if not _SHA256.match(self.model_fingerprint):
            raise ValueError("model fingerprint must be a sha256 hex digest")
        if not _SHA256.match(self.data_fingerprint):
            raise ValueError("data fingerprint must be a sha256 hex digest")


@dataclass(frozen=True)
class Membership:
    members: frozenset[str]
    as_of: date | None
    flag: str  # OK | MISSING | STALE


def membership_on(snapshots: dict[date, frozenset[str]], on: date, max_staleness_days: int) -> Membership:
    eligible = [d for d in snapshots if d <= on]
    if not eligible:
        return Membership(frozenset(), None, "MISSING")
    latest = max(eligible)
    if (on - latest).days > max_staleness_days:
        return Membership(snapshots[latest], latest, "STALE")
    return Membership(snapshots[latest], latest, "OK")


@dataclass(frozen=True)
class Holding:
    quantity: Decimal
    average_cost: Decimal

    @property
    def notional(self) -> Decimal:
        return self.quantity * self.average_cost


def adjust_for_split(holding: Holding, ratio: Decimal) -> Holding:
    """ratio is new shares per old share (2 for a 1:2 split). Cost per share is divided so notional is unchanged."""
    if ratio <= 0:
        raise ValueError("split ratio must be positive")
    return Holding(holding.quantity * ratio, holding.average_cost / ratio)


def reconciles(before: Holding, after: Holding, ledger_notional: Decimal, tolerance: Decimal = Decimal("0")) -> bool:
    return abs(after.notional - before.notional) <= tolerance and abs(after.notional - ledger_notional) <= tolerance
