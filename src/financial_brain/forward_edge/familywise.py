"""Joint stationary-block White Reality Check across a frozen strategy family (FE-0094)."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date

import numpy as np


class FamilywiseError(ValueError):
    """Invalid or incomplete strategy-family return panel."""


@dataclass(frozen=True)
class FamilyReturnRow:
    session: date
    benchmark_return: float
    strategy_net_returns: dict[str, float]

    def __post_init__(self) -> None:
        if not self.strategy_net_returns:
            raise FamilywiseError("every session must include the frozen strategy family")
        values = [self.benchmark_return, *self.strategy_net_returns.values()]
        if any(not math.isfinite(float(value)) for value in values):
            raise FamilywiseError("benchmark and strategy returns must be finite")
        if any(not key.strip() for key in self.strategy_net_returns):
            raise FamilywiseError("strategy IDs cannot be blank")


@dataclass(frozen=True)
class FamilywiseReport:
    sessions: int
    candidate_count: int
    candidate_ids: tuple[str, ...]
    observed_best_mean_alpha: float
    familywise_p_value: float
    bootstrap_critical_value: float
    expected_block_length: float
    resamples: int
    seed: int
    method: str
    report_hash: str


def white_reality_check(rows: tuple[FamilyReturnRow, ...], *, expected_block_length: float,
                        resamples: int = 5000, seed: int = 0) -> FamilywiseReport:
    """Test best observed mean net alpha against a joint stationary-block family null.

    Inputs must contain every preregistered strategy on every exchange session. The same
    sampled row indices are applied across strategies to preserve cross-strategy dependence.
    """
    if len(rows) < 2:
        raise FamilywiseError("at least two sessions are required")
    sessions = tuple(row.session for row in rows)
    if tuple(sorted(sessions)) != sessions or len(set(sessions)) != len(sessions):
        raise FamilywiseError("sessions must be unique and chronological")
    if not math.isfinite(expected_block_length) or not 1 <= expected_block_length <= len(rows):
        raise FamilywiseError("expected block length must be between one and sample size")
    if type(resamples) is not int or resamples < 200 or type(seed) is not int:
        raise FamilywiseError("at least 200 replicates and an integer seed are required")
    candidate_ids = tuple(sorted(rows[0].strategy_net_returns))
    if len(candidate_ids) < 2:
        raise FamilywiseError("family-wise correction requires at least two candidate strategies")
    if any(tuple(sorted(row.strategy_net_returns)) != candidate_ids for row in rows):
        raise FamilywiseError("each date must cover every strategy in the frozen family")
    matrix = np.asarray([[row.strategy_net_returns[key] - row.benchmark_return
                          for key in candidate_ids] for row in rows], dtype=float)
    observed_means = matrix.mean(axis=0)
    observed_best = float(observed_means.max())
    centered = matrix - observed_means
    n, width = matrix.shape
    restart_probability = 1.0 / expected_block_length
    rng = np.random.default_rng(seed)
    maxima = np.empty(resamples, dtype=float)
    for sample_index in range(resamples):
        indices = np.empty(n, dtype=np.int64)
        indices[0] = rng.integers(n)
        for position in range(1, n):
            indices[position] = (rng.integers(n) if rng.random() < restart_probability
                                  else (indices[position - 1] + 1) % n)
        maxima[sample_index] = float(centered[indices, :].mean(axis=0).max())
    p_value = float((1 + np.count_nonzero(maxima >= observed_best)) / (resamples + 1))
    critical = float(np.quantile(maxima, 0.95))
    fields = {"sessions": n, "candidate_count": width, "candidate_ids": list(candidate_ids),
              "observed_best_mean_alpha": observed_best, "familywise_p_value": p_value,
              "bootstrap_critical_value": critical,
              "expected_block_length": expected_block_length, "resamples": resamples, "seed": seed,
              "method": "JOINT_STATIONARY_BLOCK_WHITE_REALITY_CHECK"}
    digest = hashlib.sha256(json.dumps(fields, sort_keys=True, separators=(",", ":"),
                                      allow_nan=False).encode()).hexdigest()
    return FamilywiseReport(n, width, candidate_ids, observed_best, p_value, critical,
                            expected_block_length, resamples, seed,
                            "JOINT_STATIONARY_BLOCK_WHITE_REALITY_CHECK", digest)
