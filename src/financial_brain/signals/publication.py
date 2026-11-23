"""Deterministic publication quotas and event-driven signal release policy."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from math import isfinite


class Desk(StrEnum):
    SWING = "SWING"
    ETF = "ETF"
    INTRADAY = "INTRADAY"
    OPTIONS = "OPTIONS"


@dataclass(frozen=True, slots=True)
class Candidate:
    candidate_id: str
    instrument_id: str
    desk: Desk
    score: float
    expected_net_return: float
    decision_at: datetime
    eligible: bool
    event_triggered: bool = False

    def __post_init__(self) -> None:
        if not self.candidate_id.strip() or not self.instrument_id.strip():
            raise ValueError("candidate and instrument ids are required")
        if not isfinite(self.score) or not isfinite(self.expected_net_return):
            raise ValueError("candidate score and expected net return must be finite")
        if self.decision_at.tzinfo is None or self.decision_at.utcoffset() is None:
            raise ValueError("decision_at must be timezone-aware")


def publish(candidates: list[Candidate], *, as_of: datetime, swing_limit: int = 5) -> dict:
    """Return ranked daily swing/ETF lists and event-triggered intraday/options only.

    Negative-net or ineligible rows are excluded. Swing quota is a cap, never a target.
    ETF ranking is independent of equity swing ranking. Intraday/options have no forced
    trade quota and require an explicit event trigger.
    """
    if as_of.tzinfo is None or as_of.utcoffset() is None or swing_limit < 0:
        raise ValueError("as_of must be timezone-aware and swing_limit non-negative")
    valid = [item for item in candidates if item.eligible and item.expected_net_return > 0
             and item.decision_at <= as_of]
    rank = lambda item: (-item.score, -item.expected_net_return, item.instrument_id)
    return {
        "as_of": as_of,
        "swing": sorted((x for x in valid if x.desk == Desk.SWING), key=rank)[:swing_limit],
        "etf": sorted((x for x in valid if x.desk == Desk.ETF), key=rank),
        "intraday": sorted((x for x in valid if x.desk == Desk.INTRADAY and x.event_triggered), key=rank),
        "options": sorted((x for x in valid if x.desk == Desk.OPTIONS and x.event_triggered), key=rank),
    }
