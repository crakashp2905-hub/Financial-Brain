"""Decision-time, source-aware quantitative assessment attached to a signal."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime

from .contract import Signal

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class AssessmentError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class FeatureEvidence:
    name: str
    value: float
    available_at: datetime
    source_hash: str

    def __post_init__(self) -> None:
        if not self.name.strip() or not math.isfinite(self.value):
            raise AssessmentError("feature name and finite value are required")
        if self.available_at.tzinfo is None or self.available_at.utcoffset() is None:
            raise AssessmentError("feature availability timestamp must be timezone-aware")
        if not _SHA256.fullmatch(self.source_hash.lower()):
            raise AssessmentError("feature source_hash must be SHA-256")


@dataclass(frozen=True, slots=True)
class ReturnDistribution:
    horizon_sessions: int
    p10: float
    p50: float
    p90: float

    def __post_init__(self) -> None:
        if self.horizon_sessions < 1 or not all(map(math.isfinite, (self.p10, self.p50, self.p90))):
            raise AssessmentError("return distribution needs positive horizon and finite quantiles")
        if self.p10 > self.p50 or self.p50 > self.p90:
            raise AssessmentError("return quantiles must be ordered p10 <= p50 <= p90")


@dataclass(frozen=True, slots=True)
class SignalAssessment:
    signal_id: str
    features: tuple[FeatureEvidence, ...]
    forecast: ReturnDistribution
    benchmark_id: str
    benchmark_expected_return: float
    estimated_cost_bps: float
    risk_fraction: float
    contraindications: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.signal_id.strip() or not self.benchmark_id.strip():
            raise AssessmentError("signal_id and benchmark_id are required")
        values = (self.benchmark_expected_return, self.estimated_cost_bps, self.risk_fraction)
        if not all(math.isfinite(value) for value in values):
            raise AssessmentError("benchmark, cost and risk estimates must be finite")
        if self.estimated_cost_bps < 0 or self.risk_fraction < 0:
            raise AssessmentError("estimated cost and risk fraction must be non-negative")
        if any(not item.strip() for item in self.contraindications):
            raise AssessmentError("contraindication entries cannot be blank")

    def validate_for(self, signal: Signal) -> None:
        if signal.signal_id != self.signal_id:
            raise AssessmentError("assessment belongs to a different signal")
        late = [item.name for item in self.features if item.available_at > signal.decision_at]
        if late:
            raise AssessmentError(f"features unavailable at decision time: {', '.join(late)}")

    @property
    def expected_net_return(self) -> float:
        """Median forecast less estimated round-trip costs; does not deduct TER again."""
        return self.forecast.p50 - self.estimated_cost_bps / 10_000.0
