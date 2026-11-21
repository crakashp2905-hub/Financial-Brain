"""Canonical immutable signal envelope for research and paper-trading desks."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
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


__all__ = ["DeskId", "EvidenceTier", "SignalContractError", "SignalSide", "TraderSignal"]
