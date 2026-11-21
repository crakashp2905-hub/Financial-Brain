"""Two-way date/instrument cluster-robust attribution for overlapping observations (FE-0093)."""
from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from statistics import NormalDist


class ClusterAttributionError(ValueError):
    """Invalid attribution panel or too few independent clusters."""


@dataclass(frozen=True)
class AttributionObservation:
    session: date
    instrument_id: str
    net_attribution: float

    def __post_init__(self) -> None:
        if not self.instrument_id.strip() or not math.isfinite(float(self.net_attribution)):
            raise ClusterAttributionError("instrument ID and finite net attribution are required")


@dataclass(frozen=True)
class ClusterAttributionReport:
    observations: int
    date_clusters: int
    instrument_clusters: int
    mean_attribution: float
    standard_error: float
    confidence_level: float
    ci_lower: float
    ci_upper: float
    unclipped_variance: float
    variance_clipped_to_zero: bool
    method: str
    report_hash: str


def two_way_cluster_mean(observations: tuple[AttributionObservation, ...], *,
                         confidence_level: float = 0.95) -> ClusterAttributionReport:
    """Two-way cluster-robust standard error for panel mean via date + instrument - cell.

    Uses the Cameron-Gelbach-Miller inclusion/exclusion variance estimator with finite
    sample corrections and a normal reference interval. Small-cluster inference remains
    diagnostic; use preregistered block bootstrap/other methods for promotion decisions.
    """
    if len(observations) < 4:
        raise ClusterAttributionError("at least four panel observations are required")
    if not math.isfinite(confidence_level) or not 0.5 < confidence_level < 1:
        raise ClusterAttributionError("confidence_level must be in (0.5, 1)")
    cells = [(row.session, row.instrument_id) for row in observations]
    if len(set(cells)) != len(cells):
        raise ClusterAttributionError("one attribution value per date/instrument cell is required")
    date_groups: dict[date, list[float]] = defaultdict(list)
    instrument_groups: dict[str, list[float]] = defaultdict(list)
    for row in observations:
        date_groups[row.session].append(row.net_attribution)
        instrument_groups[row.instrument_id].append(row.net_attribution)
    if len(date_groups) < 2 or len(instrument_groups) < 2:
        raise ClusterAttributionError("at least two date and two instrument clusters are required")
    n = len(observations)
    mean = sum(row.net_attribution for row in observations) / n
    date_sums = [sum(value - mean for value in values) for values in date_groups.values()]
    instrument_sums = [sum(value - mean for value in values) for values in instrument_groups.values()]
    cell_sums = [row.net_attribution - mean for row in observations]
    correction_date = (len(date_groups) / (len(date_groups) - 1)) * ((n - 1) / n)
    correction_instrument = (len(instrument_groups) / (len(instrument_groups) - 1)) * ((n - 1) / n)
    correction_cell = (n / (n - 1)) * ((n - 1) / n)
    variance = (correction_date * sum(value * value for value in date_sums)
                + correction_instrument * sum(value * value for value in instrument_sums)
                - correction_cell * sum(value * value for value in cell_sums)) / (n * n)
    clipped = variance < 0.0
    standard_error = math.sqrt(max(0.0, variance))
    z = NormalDist().inv_cdf(0.5 + confidence_level / 2.0)
    lower, upper = mean - z * standard_error, mean + z * standard_error
    fields = {"observations": n, "date_clusters": len(date_groups),
              "instrument_clusters": len(instrument_groups), "mean_attribution": mean,
              "standard_error": standard_error, "confidence_level": confidence_level,
              "ci_lower": lower, "ci_upper": upper, "unclipped_variance": variance,
              "variance_clipped_to_zero": clipped,
              "method": "TWO_WAY_CLUSTER_DATE_INSTRUMENT_NORMAL_DIAGNOSTIC"}
    digest = hashlib.sha256(json.dumps(fields, sort_keys=True, separators=(",", ":"),
                                      allow_nan=False).encode()).hexdigest()
    return ClusterAttributionReport(n, len(date_groups), len(instrument_groups), mean,
                                    standard_error, confidence_level, lower, upper, variance,
                                    clipped, "TWO_WAY_CLUSTER_DATE_INSTRUMENT_NORMAL_DIAGNOSTIC", digest)
