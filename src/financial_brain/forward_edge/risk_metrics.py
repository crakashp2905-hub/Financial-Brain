"""Portfolio risk and turnover metrics with stationary-block uncertainty (FE-0095)."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date

import numpy as np


class RiskMetricsError(ValueError):
    """Invalid portfolio return, exposure or uncertainty request."""


@dataclass(frozen=True)
class RiskDay:
    session: date
    net_return: float
    traded_notional: float
    opening_equity: float

    def __post_init__(self) -> None:
        if not all(math.isfinite(float(value)) for value in
                   (self.net_return, self.traded_notional, self.opening_equity)):
            raise RiskMetricsError("daily risk inputs must be finite")
        if self.net_return <= -1:
            raise RiskMetricsError("daily loss cannot exhaust equity")
        if self.traded_notional < 0 or self.opening_equity <= 0:
            raise RiskMetricsError("notional must be non-negative and equity positive")


@dataclass(frozen=True)
class MetricInterval:
    estimate: float | None
    lower: float | None
    upper: float | None
    finite_replicates: int


@dataclass(frozen=True)
class RiskMetricsReport:
    sessions: int
    max_drawdown: MetricInterval
    max_daily_loss: MetricInterval
    conditional_drawdown_95: MetricInterval
    profit_factor: MetricInterval
    cumulative_turnover_x: MetricInterval
    confidence_level: float
    expected_block_length: float
    resamples: int
    seed: int
    report_hash: str


def _metrics(rows: tuple[RiskDay, ...]) -> tuple[float, float, float, float | None, float]:
    equity = peak = 1.0
    drawdowns = []
    for row in rows:
        equity *= 1.0 + row.net_return
        peak = max(peak, equity)
        drawdowns.append((peak - equity) / peak)
    max_dd = max(drawdowns, default=0.0)
    tail_count = max(1, math.ceil(len(drawdowns) * 0.05))
    cdd_95 = sum(sorted(drawdowns, reverse=True)[:tail_count]) / tail_count
    max_loss = max(0.0, -min((row.net_return for row in rows), default=0.0))
    winners = sum(max(row.net_return, 0.0) for row in rows)
    losers = -sum(min(row.net_return, 0.0) for row in rows)
    profit_factor = winners / losers if losers > 0 else None
    avg_equity = sum(row.opening_equity for row in rows) / len(rows)
    turnover = sum(row.traded_notional for row in rows) / avg_equity
    return max_dd, max_loss, cdd_95, profit_factor, turnover


def portfolio_risk_report(rows: tuple[RiskDay, ...], *, expected_block_length: float,
                          resamples: int = 2000, confidence_level: float = 0.95,
                          seed: int = 0) -> RiskMetricsReport:
    """Compute point metrics and stationary-block percentile intervals in chronological order."""
    if len(rows) < 2:
        raise RiskMetricsError("at least two sessions are required")
    sessions = tuple(row.session for row in rows)
    if tuple(sorted(sessions)) != sessions or len(set(sessions)) != len(sessions):
        raise RiskMetricsError("risk sessions must be unique and chronological")
    if not math.isfinite(expected_block_length) or not 1 <= expected_block_length <= len(rows):
        raise RiskMetricsError("expected block length must be between one and sample size")
    if type(resamples) is not int or resamples < 200:
        raise RiskMetricsError("at least 200 replicates are required")
    if not math.isfinite(confidence_level) or not 0.5 < confidence_level < 1:
        raise RiskMetricsError("confidence level must be in (0.5, 1)")
    if type(seed) is not int:
        raise RiskMetricsError("seed must be an integer")
    estimates = _metrics(rows)
    n = len(rows)
    rng = np.random.default_rng(seed)
    p_restart = 1.0 / expected_block_length
    draws: list[list[float]] = [[] for _ in range(5)]
    for _ in range(resamples):
        indices = np.empty(n, dtype=np.int64)
        indices[0] = rng.integers(n)
        for j in range(1, n):
            indices[j] = (rng.integers(n) if rng.random() < p_restart else (indices[j - 1] + 1) % n)
        sample = tuple(rows[int(index)] for index in indices)
        values = _metrics(sample)
        for metric_index, value in enumerate(values):
            if value is not None and math.isfinite(value):
                draws[metric_index].append(value)
    tail = (1.0 - confidence_level) / 2.0
    intervals = []
    for estimate, metric_values in zip(estimates, draws, strict=True):
        if estimate is None or len(metric_values) != resamples:
            intervals.append(MetricInterval(estimate, None, None, len(metric_values)))
        else:
            lo, hi = np.quantile(metric_values, [tail, 1 - tail]).tolist()
            intervals.append(MetricInterval(float(estimate), float(lo), float(hi), len(metric_values)))
    names = ("max_drawdown", "max_daily_loss", "conditional_drawdown_95", "profit_factor", "cumulative_turnover_x")
    metrics = {name: {"estimate": item.estimate, "lower": item.lower, "upper": item.upper,
                      "finite_replicates": item.finite_replicates}
               for name, item in zip(names, intervals, strict=True)}
    fields = {"sessions": n, "metrics": metrics, "confidence_level": confidence_level,
              "expected_block_length": expected_block_length, "resamples": resamples, "seed": seed}
    digest = hashlib.sha256(json.dumps(fields, sort_keys=True, separators=(",", ":"),
                                      allow_nan=False).encode()).hexdigest()
    return RiskMetricsReport(n, intervals[0], intervals[1], intervals[2], intervals[3], intervals[4],
                             confidence_level, expected_block_length,
                             resamples, seed, digest)
