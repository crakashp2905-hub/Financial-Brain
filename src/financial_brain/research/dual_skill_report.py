"""Keep forecasting skill and executable net economic skill as independent outcomes."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date


class DualSkillError(ValueError):
    """Invalid forecast or executable-economics panel."""


def _hash(value: str, name: str) -> None:
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise DualSkillError(f"{name} must be a lowercase SHA-256")


@dataclass(frozen=True)
class ForecastObservation:
    forecast_id: str
    probability: float
    outcome: int
    null_probability: float
    source_sha256: str

    def __post_init__(self) -> None:
        if not self.forecast_id.strip() or type(self.outcome) is not int or self.outcome not in (0, 1):
            raise DualSkillError("forecast ID and binary resolved outcome are required")
        if not all(math.isfinite(x) and 0 <= x <= 1 for x in (self.probability, self.null_probability)):
            raise DualSkillError("forecast and null probabilities must be in [0, 1]")
        _hash(self.source_sha256, "forecast source")


@dataclass(frozen=True)
class EconomicObservation:
    session: date
    strategy_net_return: float
    benchmark_net_return: float
    source_sha256: str

    def __post_init__(self) -> None:
        if not all(math.isfinite(x) and x > -1 for x in
                   (self.strategy_net_return, self.benchmark_net_return)):
            raise DualSkillError("net strategy and benchmark returns must be finite and greater than -100%")
        _hash(self.source_sha256, "economic source")


@dataclass(frozen=True)
class DualSkillReport:
    forecast_count: int
    forecast_brier: float
    null_brier: float
    forecast_brier_skill: float
    economic_sessions: int
    strategy_cumulative_net_return: float
    benchmark_cumulative_net_return: float
    cumulative_net_excess: float
    mean_session_net_excess: float
    forecast_source_hashes: tuple[str, ...]
    economic_source_hashes: tuple[str, ...]
    report_sha256: str


def _compound(values: tuple[float, ...]) -> float:
    factor = 1.0
    for value in values:
        factor *= 1.0 + value
    return factor - 1.0


def build_dual_skill_report(forecasts: tuple[ForecastObservation, ...],
                            economics: tuple[EconomicObservation, ...]) -> DualSkillReport:
    """Score forecast probabilities against a declared null and net returns against a matched benchmark.

    Forecast and economics panels are summarized separately; there is deliberately no combined
    score and no route from forecast skill alone to an economic-profit claim.
    """
    if not forecasts or not economics:
        raise DualSkillError("both independent evidence panels are required")
    forecast_ids = [row.forecast_id for row in forecasts]
    if len(set(forecast_ids)) != len(forecast_ids):
        raise DualSkillError("forecast IDs must be unique")
    sessions = tuple(row.session for row in economics)
    if tuple(sorted(sessions)) != sessions or len(set(sessions)) != len(sessions):
        raise DualSkillError("economic sessions must be unique and chronological")
    if any(row.strategy_net_return <= -1 or row.benchmark_net_return <= -1 for row in economics):
        raise DualSkillError("invalid net return")
    model_brier = sum((row.probability - row.outcome) ** 2 for row in forecasts) / len(forecasts)
    null_brier = sum((row.null_probability - row.outcome) ** 2 for row in forecasts) / len(forecasts)
    if null_brier <= 0:
        raise DualSkillError("null forecast has zero Brier loss; skill ratio is undefined")
    skill = 1.0 - model_brier / null_brier
    strategy_returns = tuple(row.strategy_net_return for row in economics)
    benchmark_returns = tuple(row.benchmark_net_return for row in economics)
    strategy_compound, benchmark_compound = _compound(strategy_returns), _compound(benchmark_returns)
    mean_excess = sum(a - b for a, b in zip(strategy_returns, benchmark_returns)) / len(economics)
    forecast_hashes = tuple(sorted({r.source_sha256 for r in forecasts}))
    economic_hashes = tuple(sorted({r.source_sha256 for r in economics}))
    body = {"forecast_count": len(forecasts), "forecast_brier": model_brier, "null_brier": null_brier,
            "forecast_brier_skill": skill, "economic_sessions": len(economics),
            "strategy_cumulative_net_return": strategy_compound,
            "benchmark_cumulative_net_return": benchmark_compound,
            "cumulative_net_excess": strategy_compound - benchmark_compound,
            "mean_session_net_excess": mean_excess,
            "forecast_source_hashes": list(forecast_hashes),
            "economic_source_hashes": list(economic_hashes)}
    digest = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                      allow_nan=False).encode()).hexdigest()
    return DualSkillReport(len(forecasts), model_brier, null_brier, skill, len(economics),
                           strategy_compound, benchmark_compound, strategy_compound - benchmark_compound,
                           mean_excess, forecast_hashes, economic_hashes, digest)


__all__ = ["DualSkillError", "DualSkillReport", "EconomicObservation", "ForecastObservation",
           "build_dual_skill_report"]
