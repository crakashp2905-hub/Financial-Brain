"""Aligned-capital champion/challenger comparison with after-cost family-wise inference (FE-0127)."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date
from typing import Mapping

import numpy as np


class ChampionComparisonError(ValueError):
    """Comparison cohort is incomplete, unaligned or lacks cost provenance."""


@dataclass(frozen=True)
class CostedPnl:
    allocated_capital: float
    gross_pnl: float
    slippage: float
    fees: float
    taxes: float
    impact: float
    financing: float
    other_costs: float
    ledger_source_sha256: str
    cost_schedule_sha256: str

    def __post_init__(self) -> None:
        values = (self.allocated_capital, self.gross_pnl, self.slippage, self.fees,
                  self.taxes, self.impact, self.financing, self.other_costs)
        if any(not math.isfinite(value) for value in values):
            raise ChampionComparisonError("P&L and capital values must be finite")
        if self.allocated_capital <= 0:
            raise ChampionComparisonError("allocated capital must be positive")
        if any(value < 0 for value in values[2:]):
            raise ChampionComparisonError("cost components must be non-negative")
        for name, digest in (("ledger", self.ledger_source_sha256),
                             ("cost schedule", self.cost_schedule_sha256)):
            if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
                raise ChampionComparisonError(f"{name} source must carry lowercase SHA-256")

    @property
    def net_pnl(self) -> float:
        return (self.gross_pnl - self.slippage - self.fees - self.taxes - self.impact
                - self.financing - self.other_costs)

    @property
    def net_return(self) -> float:
        return self.net_pnl / self.allocated_capital


@dataclass(frozen=True)
class ComparisonSession:
    session: date
    champion: CostedPnl
    challengers: Mapping[str, CostedPnl]


@dataclass(frozen=True)
class ChallengerResult:
    strategy_id: str
    mean_net_return: float
    mean_delta_vs_champion: float


@dataclass(frozen=True)
class ChampionChallengerReport:
    champion_id: str
    challenger_ids: tuple[str, ...]
    sessions: int
    aligned_capital: bool
    candidate_results: tuple[ChallengerResult, ...]
    observed_best_net_delta: float
    familywise_p_value: float
    null_critical_value_95: float
    expected_block_length: float
    resamples: int
    minimum_sessions_met: bool
    promotion_allowed: bool
    status: str
    report_hash: str


def compare_champion_to_frozen_family(*, champion_id: str,
                                      challenger_ids: tuple[str, ...],
                                      expected_sessions: tuple[date, ...],
                                      rows: tuple[ComparisonSession, ...],
                                      expected_block_length: float,
                                      resamples: int = 5_000,
                                      seed: int = 0,
                                      minimum_sessions: int = 60) -> ChampionChallengerReport:
    """Test max challenger net-return improvement over a champion under paired block resampling.

    All strategies must have the exact same session denominator, allocated capital per
    session, and explicit itemized costs. Statistical signal is descriptive and never
    directly grants promotion.
    """
    if not champion_id.strip() or not challenger_ids or any(not item.strip() for item in challenger_ids):
        raise ChampionComparisonError("champion and frozen challenger IDs are required")
    if len(set(challenger_ids)) != len(challenger_ids) or champion_id in challenger_ids:
        raise ChampionComparisonError("candidate IDs must be unique and distinct from the champion")
    if len(expected_sessions) < 2 or tuple(sorted(set(expected_sessions))) != expected_sessions:
        raise ChampionComparisonError("expected sessions must be unique and chronological")
    if len(rows) != len(expected_sessions) or tuple(row.session for row in rows) != expected_sessions:
        raise ChampionComparisonError("comparison must include every expected session in order")
    if type(resamples) is not int or resamples < 200 or type(seed) is not int:
        raise ChampionComparisonError("at least 200 resamples and an integer seed are required")
    if type(minimum_sessions) is not int or minimum_sessions < 2:
        raise ChampionComparisonError("minimum_sessions must be at least two")
    if not math.isfinite(expected_block_length) or not 1 <= expected_block_length <= len(rows):
        raise ChampionComparisonError("expected block length must be between one and sample size")

    ordered_ids = tuple(sorted(challenger_ids))
    differences: list[list[float]] = []
    candidate_returns: dict[str, list[float]] = {candidate: [] for candidate in ordered_ids}
    capital_used: list[float] = []
    cost_hashes: set[str] = set()
    ledger_hashes: list[dict[str, object]] = []
    champion_series: list[float] = []
    challenger_series: dict[str, list[float]] = {candidate: [] for candidate in ordered_ids}
    for row in rows:
        if set(row.challengers) != set(ordered_ids):
            raise ChampionComparisonError("each session must cover the complete frozen challenger family")
        capital = row.champion.allocated_capital
        capital_used.append(capital)
        cost_hashes.add(row.champion.cost_schedule_sha256)
        ledger_hashes.append({"session": row.session.isoformat(),
                              "champion": row.champion.ledger_source_sha256,
                              **{candidate: row.challengers[candidate].ledger_source_sha256
                                 for candidate in ordered_ids}})
        for candidate, pnl in row.challengers.items():
            if pnl.allocated_capital != capital:
                raise ChampionComparisonError("champion and challengers must use equal capital each session")
            cost_hashes.add(pnl.cost_schedule_sha256)
            candidate_returns[candidate].append(pnl.net_return)
            challenger_series[candidate].append(pnl.net_return)
        champion_series.append(row.champion.net_return)
        differences.append([row.challengers[candidate].net_return - row.champion.net_return
                            for candidate in ordered_ids])

    matrix = np.asarray(differences, dtype=float)
    means = matrix.mean(axis=0)
    observed_best = float(means.max())
    centered = matrix - means
    n = len(rows)
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
    results = tuple(ChallengerResult(candidate,
                                     float(np.mean(candidate_returns[candidate])),
                                     float(means[index]))
                    for index, candidate in enumerate(ordered_ids))
    sessions_met = n >= minimum_sessions
    status = ("INSUFFICIENT_SESSIONS" if not sessions_met else
              "FAMILYWISE_POSITIVE_NET_SIGNAL" if observed_best > 0 and p_value <= 0.05 else
              "NO_FAMILYWISE_CHALLENGER_IMPROVEMENT")
    payload = {"champion_id": champion_id, "challenger_ids": list(ordered_ids),
               "sessions": [item.isoformat() for item in expected_sessions],
               "aligned_capital": True, "capital_per_session": capital_used,
               "cost_schedule_hashes": sorted(cost_hashes),
               "ledger_source_hashes": ledger_hashes,
               "champion_net_returns": champion_series,
               "challenger_net_returns": challenger_series,
               "candidate_results": [item.__dict__ for item in results],
               "observed_best_net_delta": observed_best,
               "familywise_p_value": p_value, "null_critical_value_95": critical,
               "expected_block_length": expected_block_length, "resamples": resamples,
               "seed": seed, "minimum_sessions": minimum_sessions,
               "minimum_sessions_met": sessions_met, "promotion_allowed": False,
               "status": status}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                      allow_nan=False).encode()).hexdigest()
    return ChampionChallengerReport(champion_id, ordered_ids, n, True, results, observed_best,
                                    p_value, critical, expected_block_length, resamples,
                                    sessions_met, False, status, digest)
