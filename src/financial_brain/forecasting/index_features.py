"""Causal cross-index and evidence-gated derivatives features for index forecasts."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from math import isfinite
from typing import Any

from .index_market import BAR, IndexBar15m


class IndexFeatureError(ValueError):
    """Invalid time alignment, feature inputs or source-rights evidence."""


def _utc(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise IndexFeatureError(f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class DerivativeMicrostructure:
    source_id: str
    rights_evidence_sha256: str
    observed_at: datetime
    available_at: datetime
    spread_fraction: float | None = None
    volume: float | None = None
    open_interest: float | None = None
    open_interest_change: float | None = None

    def __post_init__(self) -> None:
        if not self.source_id:
            raise IndexFeatureError("derivatives feature needs a source identifier")
        digest = self.rights_evidence_sha256
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise IndexFeatureError("derivatives feature needs lowercase SHA-256 rights evidence")
        observed, available = _utc(self.observed_at, "observed_at"), _utc(self.available_at, "available_at")
        if available < observed:
            raise IndexFeatureError("derivatives feature cannot be available before observation")
        for name in ("spread_fraction", "volume", "open_interest", "open_interest_change"):
            value = getattr(self, name)
            if value is not None and not isfinite(value):
                raise IndexFeatureError(f"{name} must be finite when supplied")
        if self.spread_fraction is not None and self.spread_fraction < 0:
            raise IndexFeatureError("spread fraction cannot be negative")
        if self.volume is not None and self.volume < 0:
            raise IndexFeatureError("volume cannot be negative")
        if self.open_interest is not None and self.open_interest < 0:
            raise IndexFeatureError("open interest cannot be negative")


def causal_index_features(index_bars: Mapping[str, Sequence[IndexBar15m]], *,
                          target_index: str, sector_indices: Sequence[str],
                          decision_at: datetime,
                          derivatives: DerivativeMicrostructure | None = None) -> dict[str, Any]:
    """Build exact-time, same-session cross-index returns and sector-relative returns.

    No forward fill is used. Every contributing bar must end and be available by the
    decision time; peer bars must share the target bar's end clock and have contiguous
    prior bars. Derivatives fields require timestamped observations with rights evidence.
    """
    cutoff = _utc(decision_at, "decision_at")
    if not target_index or target_index not in index_bars:
        raise IndexFeatureError("target index series is required")
    target_known = [bar for bar in index_bars[target_index]
                    if _utc(bar.end, "bar end") <= cutoff
                    and _utc(bar.available_at, "bar availability") <= cutoff]
    if not target_known:
        raise IndexFeatureError("no target-index bar is available by the decision clock")
    current = max(target_known, key=lambda bar: bar.end)
    previous_target = next((bar for bar in index_bars[target_index]
                            if bar.end == current.end - BAR
                            and bar.end.date() == current.end.date()
                            and _utc(bar.available_at, "bar availability") <= cutoff), None)
    if previous_target is None:
        raise IndexFeatureError("target index lacks a contiguous prior bar")
    target_return = current.close / previous_target.close - 1.0
    index_returns: dict[str, float | None] = {target_index: target_return}
    sector_relative: dict[str, float | None] = {}
    for name in sector_indices:
        if not name or name == target_index:
            raise IndexFeatureError("sector indices must be distinct nonempty series names")
        candidates = [bar for bar in index_bars.get(name, ()) if bar.end == current.end
                      and _utc(bar.end, "bar end") <= cutoff
                      and _utc(bar.available_at, "bar availability") <= cutoff]
        peer_current = candidates[0] if candidates else None
        peer_previous = next((bar for bar in index_bars.get(name, ())
                              if bar.end == current.end - BAR
                              and bar.end.date() == current.end.date()
                              and _utc(bar.available_at, "bar availability") <= cutoff), None)
        peer_return = (peer_current.close / peer_previous.close - 1.0
                       if peer_current is not None and peer_previous is not None else None)
        index_returns[name] = peer_return
        sector_relative[name] = peer_return - target_return if peer_return is not None else None

    derivative_features: dict[str, float | str | None] = {
        "source_id": None, "rights_evidence_sha256": None, "observed_at": None,
        "spread_fraction": None, "volume": None, "open_interest": None,
        "open_interest_change": None,
    }
    if derivatives is not None:
        observed = _utc(derivatives.observed_at, "derivatives observed_at")
        available = _utc(derivatives.available_at, "derivatives available_at")
        if observed <= cutoff and available <= cutoff:
            derivative_features = {
                "source_id": derivatives.source_id,
                "rights_evidence_sha256": derivatives.rights_evidence_sha256,
                "observed_at": observed.isoformat(),
                "spread_fraction": derivatives.spread_fraction,
                "volume": derivatives.volume,
                "open_interest": derivatives.open_interest,
                "open_interest_change": derivatives.open_interest_change,
            }
    return {"decision_at": cutoff.isoformat(), "target_index": target_index,
            "aligned_bar_end": current.end.isoformat(),
            "target_return_15m": target_return,
            "index_returns_15m": index_returns,
            "sector_relative_returns_15m": sector_relative,
            "derivatives": derivative_features}
