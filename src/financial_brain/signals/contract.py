"""Canonical immutable, provenance-bearing signal contract (FB-0401)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class SignalValidationError(ValueError):
    pass


class SignalSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"
    NO_TRADE = "NO_TRADE"


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class Signal:
    signal_id: str
    desk_id: str
    strategy_id: str
    strategy_version: str
    strategy_hash: str
    instrument_id: str
    side: SignalSide
    as_of: datetime
    decision_at: datetime
    expires_at: datetime
    evidence_tier: str
    source_hash: str

    def __post_init__(self) -> None:
        for name in ("signal_id", "desk_id", "strategy_id", "strategy_version", "instrument_id"):
            if not getattr(self, name).strip():
                raise SignalValidationError(f"{name} is required")
        for name in ("as_of", "decision_at", "expires_at"):
            value = getattr(self, name)
            if value.tzinfo is None or value.utcoffset() is None:
                raise SignalValidationError(f"{name} must be timezone-aware")
        if self.as_of > self.decision_at:
            raise SignalValidationError("as_of cannot be later than decision_at")
        if self.expires_at <= self.decision_at:
            raise SignalValidationError("expires_at must be later than decision_at")
        for name in ("strategy_hash", "source_hash"):
            if not _SHA256.fullmatch(getattr(self, name).lower()):
                raise SignalValidationError(f"{name} must be a SHA-256 digest")
        if not self.evidence_tier.strip():
            raise SignalValidationError("evidence_tier is required")

    def as_dict(self) -> dict:
        return {"signal_id": self.signal_id, "desk_id": self.desk_id,
                "strategy_id": self.strategy_id, "strategy_version": self.strategy_version,
                "strategy_hash": self.strategy_hash.lower(), "instrument_id": self.instrument_id,
                "side": self.side.value, "as_of": self.as_of.isoformat(),
                "decision_at": self.decision_at.isoformat(), "expires_at": self.expires_at.isoformat(),
                "evidence_tier": self.evidence_tier, "source_hash": self.source_hash.lower()}
