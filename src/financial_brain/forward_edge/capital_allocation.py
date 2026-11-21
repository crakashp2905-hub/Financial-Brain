"""Frozen capital weights across strategy books and an explicit cash sleeve (FE-0104)."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Mapping


class CapitalAllocationError(ValueError):
    """Invalid frozen portfolio allocation or incomplete sub-book record."""


@dataclass(frozen=True)
class FrozenCapitalAllocation:
    allocation_id: str
    frozen_at: datetime
    first_eligible_session: date
    starting_capital: float
    book_weights: Mapping[str, float]
    cash_weight: float

    def __post_init__(self) -> None:
        if not self.allocation_id.strip() or not self.book_weights:
            raise CapitalAllocationError("allocation ID and explicit candidate books are required")
        if self.frozen_at.tzinfo is None or self.frozen_at.utcoffset() is None:
            raise CapitalAllocationError("frozen_at must be timezone aware")
        if self.frozen_at.date() >= self.first_eligible_session:
            raise CapitalAllocationError("allocation must be frozen before the first eligible session")
        if not math.isfinite(self.starting_capital) or self.starting_capital <= 0:
            raise CapitalAllocationError("starting capital must be finite and positive")
        if len(set(self.book_weights)) != len(self.book_weights) or any(not key.strip() for key in self.book_weights):
            raise CapitalAllocationError("book IDs must be unique and non-empty")
        values = [*self.book_weights.values(), self.cash_weight]
        if any(not math.isfinite(value) or value < 0 for value in values):
            raise CapitalAllocationError("allocation weights must be finite and non-negative")
        if not math.isclose(sum(values), 1.0, rel_tol=0, abs_tol=1e-9):
            raise CapitalAllocationError("candidate books plus cash weight must sum to one")

    def digest(self) -> str:
        body = {"allocation_id": self.allocation_id,
                "frozen_at": self.frozen_at.astimezone(timezone.utc).isoformat(),
                "first_eligible_session": self.first_eligible_session.isoformat(),
                "starting_capital": self.starting_capital,
                "book_weights": dict(sorted(self.book_weights.items())),
                "cash_weight": self.cash_weight}
        return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                         allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class AllocatedSession:
    session: date
    book_net_returns: Mapping[str, float]
    cash_net_return: float


@dataclass(frozen=True)
class AllocationResult:
    allocation_hash: str
    sessions: tuple[dict[str, object], ...]
    cumulative_portfolio_return: float
    retained_book_ids: tuple[str, ...]
    promotion_allowed: bool
    report_hash: str


def evaluate_frozen_allocation(allocation: FrozenCapitalAllocation,
                               sessions: tuple[AllocatedSession, ...]) -> AllocationResult:
    """Compound fixed book/cash weights; incomplete books fail instead of disappearing."""
    if not sessions:
        raise CapitalAllocationError("allocation evaluation needs session rows")
    dates = tuple(row.session for row in sessions)
    if tuple(sorted(dates)) != dates or len(set(dates)) != len(dates):
        raise CapitalAllocationError("allocation sessions must be unique and chronological")
    if dates[0] < allocation.first_eligible_session:
        raise CapitalAllocationError("allocation data predates its frozen cohort")
    weights = dict(allocation.book_weights)
    factor = 1.0
    output = []
    for row in sessions:
        if set(row.book_net_returns) != set(weights):
            missing = sorted(set(weights) - set(row.book_net_returns))
            extra = sorted(set(row.book_net_returns) - set(weights))
            raise CapitalAllocationError(f"every frozen book must remain present; missing={missing}, extra={extra}")
        if not math.isfinite(row.cash_net_return) or any(not math.isfinite(value) or value <= -1
                                                        for value in row.book_net_returns.values()):
            raise CapitalAllocationError("book and cash returns must be finite and above -100%")
        if row.cash_net_return <= -1:
            raise CapitalAllocationError("cash return cannot be at or below -100%")
        portfolio_return = sum(weights[key] * row.book_net_returns[key] for key in weights)
        portfolio_return += allocation.cash_weight * row.cash_net_return
        if portfolio_return <= -1:
            raise CapitalAllocationError("combined allocation loss cannot exhaust capital")
        factor *= 1.0 + portfolio_return
        output.append({"session": row.session.isoformat(),
                       "book_returns": dict(sorted(row.book_net_returns.items())),
                       "cash_return": row.cash_net_return,
                       "weighted_portfolio_return": portfolio_return})
    fields = {"allocation_hash": allocation.digest(), "sessions": output,
              "cumulative_portfolio_return": factor - 1,
              "retained_book_ids": sorted(weights), "promotion_allowed": False}
    digest = hashlib.sha256(json.dumps(fields, sort_keys=True, separators=(",", ":"),
                                      allow_nan=False).encode()).hexdigest()
    return AllocationResult(allocation.digest(), tuple(output), factor - 1,
                            tuple(sorted(weights)), False, digest)
