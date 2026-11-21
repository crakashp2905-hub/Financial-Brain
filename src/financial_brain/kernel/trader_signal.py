"""Canonical immutable signal envelope for research and paper-trading desks."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from uuid import UUID


class SignalContractError(ValueError):
    """A canonical signal is malformed or temporally inconsistent."""


class DeskId(StrEnum):
    SWING = "SWING"
    INTRADAY = "INTRADAY"
    OPTIONS = "OPTIONS"
    ETF = "ETF"
    MUTUAL_FUND = "MUTUAL_FUND"


class SignalSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"
    SELL_SHORT = "SELL_SHORT"
    BUY_TO_COVER = "BUY_TO_COVER"
    WATCH = "WATCH"
    NO_TRADE = "NO_TRADE"


class EvidenceTier(StrEnum):
    FIXTURE = "FIXTURE"
    REPLAY = "REPLAY"
    LOCAL_PAPER = "LOCAL_PAPER"
    ATTESTED_FORWARD = "ATTESTED_FORWARD"
    BROKER_APPROVED = "BROKER_APPROVED"


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _aware(name: str, value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise SignalContractError(f"{name} must be timezone-aware")


@dataclass(frozen=True, slots=True)
class TraderSignal:
    """Decision-time signal identity with source and strategy provenance."""

    signal_id: str
    desk_id: DeskId
    strategy_id: str
    strategy_version: str
    strategy_hash: str
    instrument_id: str
    side: SignalSide
    as_of: datetime
    decision_at: datetime
    expires_at: datetime
    evidence_tier: EvidenceTier
    source_hash: str

    def __post_init__(self) -> None:
        try:
            parsed = UUID(self.signal_id)
        except (ValueError, AttributeError) as exc:
            raise SignalContractError("signal_id must be a UUID") from exc
        if str(parsed) != self.signal_id.lower():
            raise SignalContractError("signal_id must use canonical UUID form")
        for name in ("strategy_id", "strategy_version", "instrument_id"):
            if not getattr(self, name).strip():
                raise SignalContractError(f"{name} is required")
        for name in ("strategy_hash", "source_hash"):
            if not _SHA256.fullmatch(getattr(self, name)):
                raise SignalContractError(f"{name} must be lowercase SHA-256")
        for name in ("as_of", "decision_at", "expires_at"):
            _aware(name, getattr(self, name))
        if self.as_of.astimezone(timezone.utc) > self.decision_at.astimezone(timezone.utc):
            raise SignalContractError("as_of cannot be later than decision_at")
        if self.expires_at.astimezone(timezone.utc) <= self.decision_at.astimezone(timezone.utc):
            raise SignalContractError("expires_at must be later than decision_at")


@dataclass(frozen=True, slots=True)
class DecisionFeature:
    name: str
    value: Decimal | str | bool
    observed_at: datetime
    source_hash: str

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise SignalContractError("feature name is required")
        _aware("feature observed_at", self.observed_at)
        if not _SHA256.fullmatch(self.source_hash):
            raise SignalContractError("feature source_hash must be lowercase SHA-256")
        if isinstance(self.value, Decimal) and not self.value.is_finite():
            raise SignalContractError("numeric feature values must be finite")


@dataclass(frozen=True, slots=True)
class ExpectedReturnDistribution:
    mean: Decimal
    lower_quantile: Decimal
    upper_quantile: Decimal
    confidence: Decimal
    horizon: str

    def __post_init__(self) -> None:
        for name in ("mean", "lower_quantile", "upper_quantile", "confidence"):
            try:
                value = Decimal(getattr(self, name))
            except (InvalidOperation, TypeError) as exc:
                raise SignalContractError(f"{name} must be decimal") from exc
            if not value.is_finite():
                raise SignalContractError(f"{name} must be finite")
            object.__setattr__(self, name, value)
        if self.lower_quantile > self.mean or self.mean > self.upper_quantile:
            raise SignalContractError("return quantiles must bracket the mean")
        if not Decimal(0) < self.confidence <= Decimal(1):
            raise SignalContractError("confidence must be in (0, 1]")
        if not self.horizon.strip():
            raise SignalContractError("distribution horizon is required")


@dataclass(frozen=True, slots=True)
class DecisionAssessment:
    signal_id: str
    decision_at: datetime
    features: tuple[DecisionFeature, ...]
    evidence: tuple[DecisionFeature, ...]
    expected_return: ExpectedReturnDistribution
    benchmark_id: str
    estimated_cost_bps: Decimal
    risk_budget_inr: Decimal
    expected_return_inr: Decimal
    contraindications: tuple[str, ...]

    def __post_init__(self) -> None:
        try:
            UUID(self.signal_id)
        except (ValueError, AttributeError) as exc:
            raise SignalContractError("assessment signal_id must be a UUID") from exc
        _aware("assessment decision_at", self.decision_at)
        if not self.benchmark_id.strip():
            raise SignalContractError("benchmark_id is required")
        if not self.features or not self.evidence:
            raise SignalContractError("decision-time features and evidence are required")
        if any(item.observed_at.astimezone(timezone.utc) > self.decision_at.astimezone(timezone.utc)
               for item in (*self.features, *self.evidence)):
            raise SignalContractError("assessment cannot include future features or evidence")
        for name in ("estimated_cost_bps", "risk_budget_inr", "expected_return_inr"):
            try:
                value = Decimal(getattr(self, name))
            except (InvalidOperation, TypeError) as exc:
                raise SignalContractError(f"{name} must be decimal") from exc
            if not value.is_finite() or (name != "expected_return_inr" and value < 0):
                raise SignalContractError(f"{name} is outside its valid range")
            object.__setattr__(self, name, value)
        if not self.contraindications or any(not reason.strip() for reason in self.contraindications):
            raise SignalContractError("contraindications must include reasons or explicit NONE")


__all__ = [
    "DecisionAssessment", "DecisionFeature", "DeskId", "EvidenceTier",
    "ExpectedReturnDistribution", "SignalContractError", "SignalSide", "TraderSignal",
]
