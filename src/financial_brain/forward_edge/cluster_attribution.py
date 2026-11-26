"""Two-way date/instrument cluster-robust attribution for overlapping observations (FE-0093)."""
from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from statistics import NormalDist
from typing import TypeVar

import numpy as np

_ClusterKey = TypeVar("_ClusterKey")


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


@dataclass(frozen=True)
class TradeSessionObservation:
    """One mark/realization for a trade position during a market session."""

    session: date
    trade_id: str
    net_attribution: float

    def __post_init__(self) -> None:
        if not self.trade_id.strip() or not math.isfinite(float(self.net_attribution)):
            raise ClusterAttributionError("trade ID and finite net attribution are required")


@dataclass(frozen=True)
class TradeSessionClusterReport:
    observations: int
    session_clusters: int
    trade_clusters: int
    effective_independent_observations: float
    mean_attribution: float
    standard_error: float
    confidence_level: float
    ci_lower: float
    ci_upper: float
    method: str
    report_hash: str


@dataclass(frozen=True)
class TradeSessionBootstrapReport:
    observations: int
    session_clusters: int
    trade_clusters: int
    effective_independent_observations: float
    block_length_sessions: int
    replicates: int
    seed: int
    confidence_level: float
    mean_attribution: float
    ci_lower: float
    ci_upper: float
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


def two_way_trade_session_mean(observations: tuple[TradeSessionObservation, ...], *,
                               confidence_level: float = 0.95) -> TradeSessionClusterReport:
    """Two-way cluster-robust mean by trade and session with a conservative effective N.

    The effective count is the smaller Kish effective cluster count across the two dependence
    dimensions. It is not the raw number of trade-session marks and the interval remains a
    diagnostic normal approximation, not a standalone promotion gate.
    """
    if len(observations) < 4:
        raise ClusterAttributionError("at least four trade-session observations are required")
    if not math.isfinite(confidence_level) or not 0.5 < confidence_level < 1:
        raise ClusterAttributionError("confidence_level must be in (0.5, 1)")
    cells = [(row.session, row.trade_id) for row in observations]
    if len(set(cells)) != len(cells):
        raise ClusterAttributionError("one attribution value per session/trade cell is required")
    session_groups: dict[date, list[float]] = defaultdict(list)
    trade_groups: dict[str, list[float]] = defaultdict(list)
    for row in observations:
        session_groups[row.session].append(row.net_attribution)
        trade_groups[row.trade_id].append(row.net_attribution)
    if len(session_groups) < 2 or len(trade_groups) < 2:
        raise ClusterAttributionError("at least two session and two trade clusters are required")
    n = len(observations)
    mean = sum(row.net_attribution for row in observations) / n
    session_sums = [sum(value - mean for value in values) for values in session_groups.values()]
    trade_sums = [sum(value - mean for value in values) for values in trade_groups.values()]
    cell_sums = [row.net_attribution - mean for row in observations]
    gs, gt = len(session_groups), len(trade_groups)
    correction_session = (gs / (gs - 1)) * ((n - 1) / n)
    correction_trade = (gt / (gt - 1)) * ((n - 1) / n)
    correction_cell = n / (n - 1)
    variance = (correction_session * sum(x * x for x in session_sums)
                + correction_trade * sum(x * x for x in trade_sums)
                - correction_cell * sum(x * x for x in cell_sums)) / (n * n)
    standard_error = math.sqrt(max(0.0, variance))
    z = NormalDist().inv_cdf(0.5 + confidence_level / 2.0)
    lower, upper = mean - z * standard_error, mean + z * standard_error

    def kish_effective_count(groups: dict[_ClusterKey, list[float]]) -> float:
        sizes = [len(rows) for rows in groups.values()]
        return float(sum(sizes) ** 2 / sum(size * size for size in sizes))

    effective_n = min(kish_effective_count(session_groups), kish_effective_count(trade_groups))
    fields = {"observations": n, "session_clusters": gs, "trade_clusters": gt,
              "effective_independent_observations": effective_n, "mean_attribution": mean,
              "standard_error": standard_error, "confidence_level": confidence_level,
              "ci_lower": lower, "ci_upper": upper, "method": "TWO_WAY_CLUSTER_TRADE_SESSION_KISH_MIN_DIAGNOSTIC"}
    digest = hashlib.sha256(json.dumps(fields, sort_keys=True, separators=(",", ":"),
                                      allow_nan=False).encode()).hexdigest()
    return TradeSessionClusterReport(n, gs, gt, effective_n, mean, standard_error, confidence_level,
                                     lower, upper, "TWO_WAY_CLUSTER_TRADE_SESSION_KISH_MIN_DIAGNOSTIC", digest)


def trade_session_block_bootstrap(
    observations: tuple[TradeSessionObservation, ...], *,
    block_length_sessions: int,
    replicates: int = 2_000,
    seed: int = 20261126,
    confidence_level: float = 0.95,
) -> TradeSessionBootstrapReport:
    """Bootstrap sessions in stationary blocks and trades as whole clusters.

    Each replicate multiplies the sampled session and trade cluster counts for every
    observed trade-session cell. This preserves within-trade dependence and short-range
    chronological session dependence without treating marks as independent rows. It is a
    sensitivity interval; promotion still requires a preregistered family-specific plan.
    """
    if len(observations) < 4:
        raise ClusterAttributionError("at least four trade-session observations are required")
    if type(block_length_sessions) is not int or block_length_sessions < 1:
        raise ClusterAttributionError("session block length must be a positive integer")
    if type(replicates) is not int or replicates < 200:
        raise ClusterAttributionError("at least 200 bootstrap replicates are required")
    if type(seed) is not int or seed < 0:
        raise ClusterAttributionError("bootstrap seed must be a nonnegative integer")
    if not math.isfinite(confidence_level) or not 0.5 < confidence_level < 1:
        raise ClusterAttributionError("confidence_level must be in (0.5, 1)")
    cells = [(row.session, row.trade_id) for row in observations]
    if len(set(cells)) != len(cells):
        raise ClusterAttributionError("one attribution value per session/trade cell is required")
    sessions = sorted({row.session for row in observations})
    trades = sorted({row.trade_id for row in observations})
    if len(sessions) < 2 or len(trades) < 2:
        raise ClusterAttributionError("at least two session and two trade clusters are required")
    if len(observations) < 2 * min(len(sessions), len(trades)):
        raise ClusterAttributionError("too few observed cells for two-way cluster resampling")

    by_session = {session: index for index, session in enumerate(sessions)}
    by_trade = {trade: index for index, trade in enumerate(trades)}
    rows = sorted(observations, key=lambda row: (row.session, row.trade_id))
    session_index = np.asarray([by_session[row.session] for row in rows], dtype=np.int64)
    trade_index = np.asarray([by_trade[row.trade_id] for row in rows], dtype=np.int64)
    values = np.asarray([row.net_attribution for row in rows], dtype=np.float64)
    rng = np.random.default_rng(seed)
    bootstrap_means = np.empty(replicates, dtype=np.float64)
    session_count = len(sessions)
    trade_count = len(trades)
    restart_probability = min(1.0, 1.0 / block_length_sessions)
    for replicate in range(replicates):
        sampled_sessions = np.empty(session_count, dtype=np.int64)
        sampled_sessions[0] = rng.integers(session_count)
        for position in range(1, session_count):
            if rng.random() < restart_probability:
                sampled_sessions[position] = rng.integers(session_count)
            else:
                sampled_sessions[position] = (sampled_sessions[position - 1] + 1) % session_count
        session_weights = np.bincount(sampled_sessions, minlength=session_count)
        sampled_trades = rng.integers(trade_count, size=trade_count)
        trade_weights = np.bincount(sampled_trades, minlength=trade_count)
        weights = session_weights[session_index] * trade_weights[trade_index]
        denominator = int(weights.sum())
        if denominator <= 0:
            raise ClusterAttributionError("two-way bootstrap produced an empty resample")
        bootstrap_means[replicate] = float(np.dot(weights, values) / denominator)

    session_sizes = np.bincount(session_index, minlength=session_count)
    trade_sizes = np.bincount(trade_index, minlength=trade_count)
    effective_n = min(float(session_sizes.sum() ** 2 / np.dot(session_sizes, session_sizes)),
                      float(trade_sizes.sum() ** 2 / np.dot(trade_sizes, trade_sizes)))
    alpha = (1 - confidence_level) / 2
    lower, upper = np.quantile(bootstrap_means, [alpha, 1 - alpha], method="linear")
    mean = float(values.mean())
    fields = {
        "observations": len(rows), "session_clusters": session_count, "trade_clusters": trade_count,
        "effective_independent_observations": effective_n,
        "block_length_sessions": block_length_sessions, "replicates": replicates,
        "seed": seed, "confidence_level": confidence_level, "mean_attribution": mean,
        "ci_lower": float(lower), "ci_upper": float(upper),
        "method": "TWO_WAY_STATIONARY_SESSION_BLOCK_X_TRADE_CLUSTER_PERCENTILE",
        "observation_hash": hashlib.sha256(json.dumps(
            [[row.session.isoformat(), row.trade_id, row.net_attribution] for row in rows],
            separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest(),
    }
    digest = hashlib.sha256(json.dumps(fields, sort_keys=True, separators=(",", ":"),
                                      allow_nan=False).encode()).hexdigest()
    return TradeSessionBootstrapReport(
        len(rows), session_count, trade_count, effective_n, block_length_sessions,
        replicates, seed, confidence_level, mean, float(lower), float(upper),
        str(fields["method"]), digest,
    )
