"""Context matched sleeve allocation from matured net outcomes and frozen paper intents.

This is a deterministic research/paper primitive. It does not train on pending labels,
route to a broker, or alter intents after the decision is frozen.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Mapping

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ContextualAllocationError(ValueError):
    """Invalid delayed reward, context, policy or frozen paper intent."""


def _utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ContextualAllocationError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _digest(value: str, name: str) -> None:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ContextualAllocationError(f"{name} must be a lowercase SHA-256")


def _canonical_context(value: Mapping[str, str]) -> Mapping[str, str]:
    if not value or any(not isinstance(k, str) or not k.strip() or
                        not isinstance(v, str) or not v.strip() for k, v in value.items()):
        raise ContextualAllocationError("context requires non-empty string keys and values")
    return MappingProxyType(dict(sorted(value.items())))


def _hash(body: object) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class MaturedSleeveReward:
    """A completed, net-of-cost outcome released for learning after its maturation clock."""

    outcome_id: str
    sleeve_id: str
    context: Mapping[str, str]
    decision_at: datetime
    matured_at: datetime
    net_return: float
    source_sha256: str

    def __post_init__(self) -> None:
        if not self.outcome_id.strip() or not self.sleeve_id.strip():
            raise ContextualAllocationError("outcome and sleeve identities are required")
        decision, matured = _utc(self.decision_at, "decision_at"), _utc(self.matured_at, "matured_at")
        if matured < decision:
            raise ContextualAllocationError("reward cannot mature before its decision")
        if not math.isfinite(self.net_return) or self.net_return <= -1:
            raise ContextualAllocationError("net return must be finite and above -100%")
        _digest(self.source_sha256, "outcome source hash")
        object.__setattr__(self, "decision_at", decision)
        object.__setattr__(self, "matured_at", matured)
        object.__setattr__(self, "context", _canonical_context(self.context))


@dataclass(frozen=True, slots=True)
class SleeveOrderCandidate:
    """Pre-sized broker-independent paper intent supplied by a risk-qualified sleeve."""

    sleeve_id: str
    strategy_version: str
    instrument_id: str
    side: str
    quantity: int
    tick_size: str
    decision_at: datetime
    eligible_at: datetime
    context: Mapping[str, str]
    risk_qualified: bool
    limit_price: str | None = None

    def __post_init__(self) -> None:
        if not all(x.strip() for x in (self.sleeve_id, self.strategy_version, self.instrument_id)):
            raise ContextualAllocationError("sleeve, strategy version and instrument are required")
        if self.side not in {"BUY", "SELL"} or type(self.quantity) is not int or self.quantity <= 0:
            raise ContextualAllocationError("side and positive whole quantity are required")
        decision, eligible = _utc(self.decision_at, "decision_at"), _utc(self.eligible_at, "eligible_at")
        if eligible < decision:
            raise ContextualAllocationError("intent eligibility cannot precede its decision")
        for label, raw in (("tick size", self.tick_size), ("limit price", self.limit_price)):
            if raw is None:
                continue
            try:
                parsed = Decimal(raw)
            except (InvalidOperation, TypeError, ValueError) as exc:
                raise ContextualAllocationError(f"{label} must be a valid decimal string") from exc
            if not parsed.is_finite() or parsed <= 0:
                raise ContextualAllocationError(f"{label} must be finite and positive")
        if Decimal(self.tick_size) <= 0:
            raise ContextualAllocationError("tick size must be positive")
        if type(self.risk_qualified) is not bool or not self.risk_qualified:
            raise ContextualAllocationError("only risk-qualified paper candidates may be allocated")
        object.__setattr__(self, "decision_at", decision)
        object.__setattr__(self, "eligible_at", eligible)
        object.__setattr__(self, "context", _canonical_context(self.context))


@dataclass(frozen=True, slots=True)
class FrozenSleeveOrderIntent:
    """Immutable execution-neutral intent bound to an allocation and decision context."""

    sleeve_id: str
    strategy_version: str
    instrument_id: str
    side: str
    quantity: int
    tick_size: str
    limit_price: str | None
    decision_at: datetime
    eligible_at: datetime
    weight: float
    allocation_sha256: str
    context_sha256: str
    intent_sha256: str


@dataclass(frozen=True, slots=True)
class ContextualAllocation:
    as_of: datetime
    context_sha256: str
    weights: Mapping[str, float]
    cash_weight: float
    matched_observation_counts: Mapping[str, int]
    matched_mean_net_returns: Mapping[str, float]
    intents: tuple[FrozenSleeveOrderIntent, ...]
    allocation_sha256: str
    promotion_allowed: bool = False


@dataclass(frozen=True, slots=True)
class ContextualSleeveAllocator:
    minimum_matured_observations: int = 5
    maximum_sleeve_weight: float = 0.25

    def __post_init__(self) -> None:
        if type(self.minimum_matured_observations) is not int or self.minimum_matured_observations < 1:
            raise ContextualAllocationError("minimum matured observations must be a positive integer")
        if not math.isfinite(self.maximum_sleeve_weight) or not 0 < self.maximum_sleeve_weight <= 1:
            raise ContextualAllocationError("maximum sleeve weight must be in (0, 1]")

    def allocate(
        self,
        *,
        observations: tuple[MaturedSleeveReward, ...],
        candidates: tuple[SleeveOrderCandidate, ...],
        context: Mapping[str, str],
        as_of: datetime,
        cash_return_hurdle: float,
    ) -> ContextualAllocation:
        """Use only exact-context outcomes both decided and matured by ``as_of``.

        Positive mean net return above the explicit cash hurdle gets a capped proportional
        weight. Unallocated weight remains cash. Candidates and generated intents are frozen
        as a content-hashed decision; later reward arrivals can affect only a later allocation.
        """
        now = _utc(as_of, "as_of")
        if not math.isfinite(cash_return_hurdle) or cash_return_hurdle <= -1:
            raise ContextualAllocationError("cash hurdle must be finite and above -100%")
        frozen_context = _canonical_context(context)
        context_body = dict(frozen_context)
        context_hash = _hash(context_body)
        if len({o.outcome_id for o in observations}) != len(observations):
            raise ContextualAllocationError("reward outcome IDs must be unique")
        if len({c.sleeve_id for c in candidates}) != len(candidates):
            raise ContextualAllocationError("candidate sleeve IDs must be unique")
        if any(c.decision_at > now or c.eligible_at < now for c in candidates):
            raise ContextualAllocationError("candidate must be decided and eligible at the allocation freeze time")
        if any(dict(c.context) != context_body for c in candidates):
            raise ContextualAllocationError("candidate context must exactly match the frozen decision context")

        by_sleeve: dict[str, list[float]] = {candidate.sleeve_id: [] for candidate in candidates}
        for row in observations:
            if row.decision_at < now and row.matured_at <= now and dict(row.context) == context_body:
                if row.sleeve_id in by_sleeve:
                    by_sleeve[row.sleeve_id].append(row.net_return)
        counts = {sleeve: len(values) for sleeve, values in sorted(by_sleeve.items())}
        means = {sleeve: sum(values) / len(values) for sleeve, values in sorted(by_sleeve.items()) if values}
        edge = {sleeve: max(0.0, mean - cash_return_hurdle)
                if counts[sleeve] >= self.minimum_matured_observations else 0.0
                for sleeve, mean in means.items()}
        edge = {sleeve: value for sleeve, value in edge.items() if value > 0}
        weights: dict[str, float] = {}
        remaining = 1.0
        active = dict(edge)
        while active and remaining > 1e-12:
            total_edge = sum(active.values())
            proposed = {sleeve: remaining * value / total_edge for sleeve, value in active.items()}
            capped = [sleeve for sleeve, value in proposed.items()
                      if value >= self.maximum_sleeve_weight - 1e-12]
            if not capped:
                weights.update(proposed)
                remaining = 0.0
                break
            for sleeve in capped:
                weights[sleeve] = self.maximum_sleeve_weight
                remaining -= self.maximum_sleeve_weight
                active.pop(sleeve)
        weights = dict(sorted(weights.items()))
        cash_weight = max(0.0, 1.0 - sum(weights.values()))
        allocation_body = {"as_of": now.isoformat(), "context_sha256": context_hash,
                           "cash_return_hurdle": cash_return_hurdle, "weights": weights,
                           "cash_weight": cash_weight, "matched_observation_counts": counts,
                           "matched_mean_net_returns": means}
        allocation_hash = _hash(allocation_body)
        frozen_intents = []
        for candidate in sorted(candidates, key=lambda item: item.sleeve_id):
            weight = weights.get(candidate.sleeve_id, 0.0)
            if weight <= 0:
                continue
            intent_body = {"sleeve_id": candidate.sleeve_id,
                           "strategy_version": candidate.strategy_version,
                           "instrument_id": candidate.instrument_id, "side": candidate.side,
                           "quantity": candidate.quantity, "tick_size": candidate.tick_size,
                           "limit_price": candidate.limit_price,
                           "decision_at": candidate.decision_at.isoformat(),
                           "eligible_at": candidate.eligible_at.isoformat(), "weight": weight,
                           "allocation_sha256": allocation_hash, "context_sha256": context_hash}
            frozen_intents.append(FrozenSleeveOrderIntent(
                sleeve_id=candidate.sleeve_id, strategy_version=candidate.strategy_version,
                instrument_id=candidate.instrument_id, side=candidate.side,
                quantity=candidate.quantity, tick_size=candidate.tick_size,
                limit_price=candidate.limit_price, decision_at=candidate.decision_at,
                eligible_at=candidate.eligible_at, weight=weight,
                allocation_sha256=allocation_hash, context_sha256=context_hash,
                intent_sha256=_hash(intent_body)))
        return ContextualAllocation(now, context_hash, MappingProxyType(weights), cash_weight,
                                    MappingProxyType(counts), MappingProxyType(means),
                                    tuple(frozen_intents), allocation_hash)


__all__ = ["ContextualAllocation", "ContextualAllocationError", "ContextualSleeveAllocator",
           "FrozenSleeveOrderIntent", "MaturedSleeveReward", "SleeveOrderCandidate"]

