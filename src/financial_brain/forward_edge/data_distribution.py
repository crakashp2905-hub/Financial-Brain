"""Separate market-price regime changes from provider/data-distribution shifts.

This module scores two independently observed windows. It does not infer a price regime
from feed health, and it never treats a provider outage or schema change as alpha evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math
import re


class DistributionError(ValueError):
    """Window metrics are malformed or incomparable."""


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _window(start: datetime, end: datetime, label: str) -> None:
    if (start.tzinfo is None or start.utcoffset() is None
            or end.tzinfo is None or end.utcoffset() is None or end <= start):
        raise DistributionError(f"{label} window needs aware increasing timestamps")


@dataclass(frozen=True)
class PriceWindow:
    start: datetime
    end: datetime
    observations: int
    realized_volatility: float
    net_return: float

    def __post_init__(self) -> None:
        _window(self.start, self.end, "price")
        if type(self.observations) is not int or self.observations < 2:
            raise DistributionError("price window needs at least two observations")
        if (not math.isfinite(self.realized_volatility) or self.realized_volatility < 0
                or not math.isfinite(self.net_return)):
            raise DistributionError("price-window metrics must be finite and volatility non-negative")


@dataclass(frozen=True)
class ProviderWindow:
    start: datetime
    end: datetime
    observations: int
    quote_coverage: float
    stale_fraction: float
    out_of_order_fraction: float
    missing_payload_fraction: float
    source_mix: tuple[tuple[str, float], ...]
    schema_sha256: str

    def __post_init__(self) -> None:
        _window(self.start, self.end, "provider")
        if type(self.observations) is not int or self.observations < 1:
            raise DistributionError("provider window needs at least one observation")
        fractions = (self.quote_coverage, self.stale_fraction, self.out_of_order_fraction,
                     self.missing_payload_fraction)
        if any(not math.isfinite(value) or not 0 <= value <= 1 for value in fractions):
            raise DistributionError("provider rates must be finite fractions in [0, 1]")
        if not self.source_mix or any(not name.strip() or not math.isfinite(weight) or weight < 0
                                      for name, weight in self.source_mix):
            raise DistributionError("source mix requires named sources and non-negative weights")
        if len({name for name, _ in self.source_mix}) != len(self.source_mix):
            raise DistributionError("source mix contains duplicate source names")
        if not math.isclose(sum(weight for _, weight in self.source_mix), 1.0, abs_tol=1e-6):
            raise DistributionError("source-mix weights must sum to one")
        if not _SHA256.fullmatch(self.schema_sha256):
            raise DistributionError("schema fingerprint must be a lowercase SHA-256")


@dataclass(frozen=True)
class DistributionThresholds:
    minimum_observations: int = 20
    volatility_relative_change: float = 0.5
    absolute_return_change: float = 0.03
    quote_coverage_change: float = 0.10
    stale_fraction_change: float = 0.05
    out_of_order_fraction_change: float = 0.01
    missing_payload_fraction_change: float = 0.02
    source_mix_total_variation: float = 0.20

    def __post_init__(self) -> None:
        if type(self.minimum_observations) is not int or self.minimum_observations < 2:
            raise DistributionError("minimum observations must be at least two")
        values = (self.volatility_relative_change, self.absolute_return_change,
                  self.quote_coverage_change, self.stale_fraction_change,
                  self.out_of_order_fraction_change, self.missing_payload_fraction_change,
                  self.source_mix_total_variation)
        if any(not math.isfinite(value) or value < 0 for value in values):
            raise DistributionError("shift thresholds must be finite and non-negative")


@dataclass(frozen=True)
class DistributionComparison:
    price_regime_shift: bool
    provider_distribution_shift: bool
    price_reasons: tuple[str, ...]
    provider_reasons: tuple[str, ...]
    source_mix_total_variation: float
    status: str


def _mix_distance(left: tuple[tuple[str, float], ...], right: tuple[tuple[str, float], ...]) -> float:
    a, b = dict(left), dict(right)
    return 0.5 * sum(abs(a.get(key, 0.0) - b.get(key, 0.0)) for key in a.keys() | b.keys())


def compare_windows(
    reference_price: PriceWindow,
    current_price: PriceWindow,
    reference_provider: ProviderWindow,
    current_provider: ProviderWindow,
    *,
    thresholds: DistributionThresholds = DistributionThresholds(),
) -> DistributionComparison:
    """Compare price behavior and provider behavior on matched calendar windows.

    The two booleans are independent by design: a provider shift cannot imply a price
    regime shift, and a price regime shift cannot conceal a degraded feed.
    """
    windows = (reference_price, current_price, reference_provider, current_provider)
    for row in windows[1:]:
        if row.start != reference_price.start or row.end != reference_price.end:
            raise DistributionError("all reference/current windows must use the same observation interval")
    if min(reference_price.observations, current_price.observations,
           reference_provider.observations, current_provider.observations) < thresholds.minimum_observations:
        return DistributionComparison(False, False, (), (), 0.0, "INSUFFICIENT_SUPPORT")

    price_reasons: list[str] = []
    reference_vol = reference_price.realized_volatility
    vol_change = (abs(current_price.realized_volatility - reference_vol) / reference_vol
                  if reference_vol > 0 else (math.inf if current_price.realized_volatility > 0 else 0.0))
    if vol_change >= thresholds.volatility_relative_change:
        price_reasons.append("REALIZED_VOLATILITY_SHIFT")
    if abs(current_price.net_return - reference_price.net_return) >= thresholds.absolute_return_change:
        price_reasons.append("RETURN_DISTRIBUTION_SHIFT")

    mix_distance = _mix_distance(reference_provider.source_mix, current_provider.source_mix)
    provider_reasons: list[str] = []
    comparisons = (
        (abs(current_provider.quote_coverage - reference_provider.quote_coverage),
         thresholds.quote_coverage_change, "QUOTE_COVERAGE_SHIFT"),
        (abs(current_provider.stale_fraction - reference_provider.stale_fraction),
         thresholds.stale_fraction_change, "STALE_QUOTE_RATE_SHIFT"),
        (abs(current_provider.out_of_order_fraction - reference_provider.out_of_order_fraction),
         thresholds.out_of_order_fraction_change, "OUT_OF_ORDER_RATE_SHIFT"),
        (abs(current_provider.missing_payload_fraction - reference_provider.missing_payload_fraction),
         thresholds.missing_payload_fraction_change, "MISSING_PAYLOAD_RATE_SHIFT"),
        (mix_distance, thresholds.source_mix_total_variation, "SOURCE_MIX_SHIFT"),
    )
    for difference, limit, reason in comparisons:
        if difference >= limit:
            provider_reasons.append(reason)
    if current_provider.schema_sha256 != reference_provider.schema_sha256:
        provider_reasons.append("SCHEMA_FINGERPRINT_CHANGED")

    price_shift = bool(price_reasons)
    provider_shift = bool(provider_reasons)
    status = ("BOTH_SHIFTED" if price_shift and provider_shift else
              "PRICE_REGIME_SHIFT" if price_shift else
              "PROVIDER_DISTRIBUTION_SHIFT" if provider_shift else "STABLE")
    return DistributionComparison(price_shift, provider_shift, tuple(price_reasons),
                                  tuple(provider_reasons), mix_distance, status)


__all__ = [
    "DistributionComparison", "DistributionError", "DistributionThresholds", "PriceWindow",
    "ProviderWindow", "compare_windows",
]
