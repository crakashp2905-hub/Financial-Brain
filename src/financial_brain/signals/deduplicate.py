"""Version-aware, price-aware signal alert deduplication policy."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from math import isfinite


class DedupeError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class AlertKey:
    strategy_id: str
    strategy_version: str
    instrument_id: str
    side: str
    decision_at: datetime
    reference_price: float

    def __post_init__(self) -> None:
        if not all(value.strip() for value in
                   (self.strategy_id, self.strategy_version, self.instrument_id, self.side)):
            raise DedupeError("strategy, version, instrument and side are required")
        if self.decision_at.tzinfo is None or self.decision_at.utcoffset() is None:
            raise DedupeError("decision_at must be timezone-aware")
        if not isfinite(self.reference_price) or self.reference_price <= 0:
            raise DedupeError("reference_price must be finite and positive")


def suppress(candidate: AlertKey, prior: list[AlertKey], *, window: timedelta,
             material_price_change_pct: float) -> tuple[bool, str]:
    if window.total_seconds() < 0 or not isfinite(material_price_change_pct) \
            or material_price_change_pct < 0:
        raise DedupeError("window and material price threshold must be non-negative")
    matching = [item for item in prior if
                (item.strategy_id, item.strategy_version, item.instrument_id, item.side) ==
                (candidate.strategy_id, candidate.strategy_version,
                 candidate.instrument_id, candidate.side)]
    if any(candidate.decision_at < item.decision_at for item in prior):
        raise DedupeError("candidate event time precedes an existing alert")
    for item in matching:
        age = candidate.decision_at - item.decision_at
        if age <= window:
            movement = abs(candidate.reference_price / item.reference_price - 1.0) * 100
            if movement <= material_price_change_pct:
                return True, "DUPLICATE_WITHIN_VERSION_AND_PRICE_BAND"
    return False, "NEW_VERSION_PRICE_OR_TIME_WINDOW"
