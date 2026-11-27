"""Descriptive, non-selecting fragility slices for frozen net outcomes."""
from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date


class FragilityError(ValueError):
    """Invalid or incomplete fragility evidence."""


_DIMENSIONS = ("source_date", "session", "instrument_bucket", "regime", "exposure_bucket", "holding_bucket",
               "cost_level", "slippage_bucket", "exclusion_filter")


@dataclass(frozen=True)
class NetOutcomeSlice:
    outcome_id: str
    session: date
    source_date: date
    instrument_id: str
    instrument_bucket: str
    regime: str
    exposure_bucket: str
    holding_bucket: str
    cost_level: str
    slippage_bucket: str
    exclusion_filter: str
    net_return: float
    source_sha256: str

    def __post_init__(self) -> None:
        strings = (self.outcome_id, self.instrument_id, self.instrument_bucket, self.regime,
                   self.exposure_bucket, self.holding_bucket, self.cost_level,
                   self.slippage_bucket, self.exclusion_filter)
        if any(not isinstance(value, str) or not value.strip() for value in strings):
            raise FragilityError("every outcome and diagnostic dimension must be explicit (use UNKNOWN)")
        if not math.isfinite(self.net_return) or self.net_return < -1:
            raise FragilityError("net return must be finite and cannot be below -100%")
        if len(self.source_sha256) != 64 or any(c not in "0123456789abcdef" for c in self.source_sha256):
            raise FragilityError("source identity must be lowercase SHA-256")


@dataclass(frozen=True)
class FragilityReport:
    observations: int
    minimum_group_size: int
    dimension_summaries: dict[str, dict[str, dict[str, float | int | str]]]
    top_instrument_concentration: dict[str, float | int]
    input_sha256: str
    report_sha256: str


def _summary(values: list[float], min_group_size: int) -> dict[str, float | int | str]:
    ordered = sorted(values)
    n = len(ordered)
    return {"observations": n, "mean_net_return": sum(ordered) / n,
            "median_net_return": ordered[n // 2] if n % 2 else (ordered[n // 2 - 1] + ordered[n // 2]) / 2,
            "win_rate": sum(x > 0 for x in ordered) / n,
            "net_return_sum": sum(ordered), "interpretation": "DESCRIPTIVE" if n >= min_group_size else "LOW_SAMPLE"}


def build_fragility_report(outcomes: tuple[NetOutcomeSlice, ...], *, min_group_size: int = 20) -> FragilityReport:
    """Report outcome dispersion by every frozen diagnostic dimension, without winner selection."""
    if not outcomes:
        raise FragilityError("outcomes are required")
    if type(min_group_size) is not int or min_group_size < 2:
        raise FragilityError("minimum group size must be at least two")
    ids = [row.outcome_id for row in outcomes]
    if len(set(ids)) != len(ids):
        raise FragilityError("outcome IDs must be unique")
    dimension_summaries: dict[str, dict[str, dict[str, float | int | str]]] = {}
    for dimension in _DIMENSIONS:
        groups: dict[str, list[float]] = defaultdict(list)
        for row in outcomes:
            value = getattr(row, dimension).isoformat() if dimension in {"session", "source_date"} else str(getattr(row, dimension))
            groups[value].append(row.net_return)
        dimension_summaries[dimension] = {key: _summary(values, min_group_size)
                                          for key, values in sorted(groups.items())}
    by_instrument: dict[str, float] = defaultdict(float)
    for row in outcomes:
        by_instrument[row.instrument_id] += row.net_return
    total_positive = sum(value for value in by_instrument.values() if value > 0)
    top_five = sorted(by_instrument.values(), reverse=True)[:5]
    concentration = {"instruments": len(by_instrument), "top_five_positive_share":
                     sum(max(value, 0.0) for value in top_five) / total_positive if total_positive > 0 else 0.0}
    input_body = [{"outcome_id": row.outcome_id, "session": row.session.isoformat(),
                   "source_date": row.source_date.isoformat(),
                   "instrument_id": row.instrument_id, "instrument_bucket": row.instrument_bucket,
                   "regime": row.regime, "exposure_bucket": row.exposure_bucket,
                   "holding_bucket": row.holding_bucket, "cost_level": row.cost_level,
                   "slippage_bucket": row.slippage_bucket, "exclusion_filter": row.exclusion_filter,
                   "net_return": row.net_return, "source_sha256": row.source_sha256}
                  for row in sorted(outcomes, key=lambda item: item.outcome_id)]
    input_hash = hashlib.sha256(json.dumps(input_body, sort_keys=True, separators=(",", ":"),
                                           allow_nan=False).encode()).hexdigest()
    report_body = {"observations": len(outcomes), "minimum_group_size": min_group_size,
                   "dimension_summaries": dimension_summaries,
                   "top_instrument_concentration": concentration, "input_sha256": input_hash}
    report_hash = hashlib.sha256(json.dumps(report_body, sort_keys=True, separators=(",", ":"),
                                            allow_nan=False).encode()).hexdigest()
    return FragilityReport(len(outcomes), min_group_size, dimension_summaries, concentration,
                           input_hash, report_hash)


__all__ = ["FragilityError", "FragilityReport", "NetOutcomeSlice", "build_fragility_report"]
