"""Reproducible stationary-block bootstrap confidence intervals for net alpha (FE-0092)."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date

import numpy as np


class BootstrapError(ValueError):
    """Invalid or insufficient return series for dependence-aware inference."""


@dataclass(frozen=True)
class DatedAlpha:
    session: date
    net_alpha: float

    def __post_init__(self) -> None:
        if not math.isfinite(float(self.net_alpha)):
            raise BootstrapError("net alpha must be finite")


@dataclass(frozen=True)
class BootstrapReport:
    observations: int
    mean_net_alpha: float
    confidence_level: float
    ci_lower: float
    ci_upper: float
    expected_block_length: float
    resamples: int
    seed: int
    method: str
    promotion_inference_allowed: bool
    report_hash: str


def stationary_block_bootstrap_mean(observations: tuple[DatedAlpha, ...], *,
                                    expected_block_length: float,
                                    resamples: int = 5000,
                                    confidence_level: float = 0.95,
                                    seed: int = 0) -> BootstrapReport:
    """Percentile CI for mean alpha using random-length circular stationary blocks.

    Each observation is a chronologically ordered daily or overlapping-horizon return.
    The geometric block length preserves local serial dependence in expectation. This is
    an analysis primitive; it does not correct family-wise search by itself.
    """
    if len(observations) < 2:
        raise BootstrapError("at least two ordered observations are required")
    sessions = tuple(row.session for row in observations)
    if tuple(sorted(sessions)) != sessions or len(set(sessions)) != len(sessions):
        raise BootstrapError("observations must have unique chronological session dates")
    if not math.isfinite(expected_block_length) or not 1 <= expected_block_length <= len(observations):
        raise BootstrapError("expected block length must be between one and sample size")
    if type(resamples) is not int or resamples < 200:
        raise BootstrapError("at least 200 bootstrap replicates are required")
    if not math.isfinite(confidence_level) or not 0.5 < confidence_level < 1:
        raise BootstrapError("confidence_level must be in (0.5, 1)")
    if type(seed) is not int:
        raise BootstrapError("seed must be an integer")

    values = np.asarray([row.net_alpha for row in observations], dtype=float)
    n = len(values)
    restart_probability = 1.0 / expected_block_length
    rng = np.random.default_rng(seed)
    means = np.empty(resamples, dtype=float)
    for sample_index in range(resamples):
        indices = np.empty(n, dtype=np.int64)
        indices[0] = rng.integers(n)
        for position in range(1, n):
            if rng.random() < restart_probability:
                indices[position] = rng.integers(n)
            else:
                indices[position] = (indices[position - 1] + 1) % n
        means[sample_index] = values[indices].mean()
    tail = (1.0 - confidence_level) / 2.0
    lower, upper = np.quantile(means, [tail, 1.0 - tail]).tolist()
    fields = {"observations": n, "mean_net_alpha": float(values.mean()),
              "confidence_level": confidence_level, "ci_lower": float(lower),
              "ci_upper": float(upper), "expected_block_length": expected_block_length,
              "resamples": resamples, "seed": seed,
              "method": "STATIONARY_BLOCK_PERCENTILE_CI",
              "promotion_inference_allowed": False}
    digest = hashlib.sha256(json.dumps(fields, sort_keys=True, separators=(",", ":"),
                                      allow_nan=False).encode()).hexdigest()
    return BootstrapReport(n, float(values.mean()), confidence_level, float(lower), float(upper),
                           expected_block_length, resamples, seed,
                           "STATIONARY_BLOCK_PERCENTILE_CI", False, digest)
