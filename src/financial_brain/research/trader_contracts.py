"""Research-only decision contracts and conservative evaluation helpers.

These functions do not submit orders, confer strategy validation, or estimate alpha
from action-classification accuracy.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime

import numpy as np


@dataclass(frozen=True)
class OpportunityEstimate:
    instrument: str
    direction: str
    decision_ts: datetime
    horizon_sessions: int
    strategy: str
    regime: str
    gross_return: float
    benchmark_return: float
    fees: float
    spread: float
    slippage: float
    impact: float
    tail_risk: float
    portfolio_contribution: float
    reliability: float | None
    liquidity_ok: bool
    supporting_evidence: tuple[str, ...] = ()
    contradictory_evidence: tuple[str, ...] = ()
    invalidation: tuple[str, ...] = ()
    calibration_status: str = "unverified"
    forecast_quantiles: tuple[tuple[float, float], ...] = ()

    def __post_init__(self) -> None:
        if self.decision_ts.tzinfo is None or self.horizon_sessions < 1:
            raise ValueError("timestamp must be timezone-aware and horizon positive")
        if self.reliability is not None and not 0 <= self.reliability <= 1:
            raise ValueError("reliability must be in [0, 1]")
        if self.calibration_status not in {"unverified", "calibrated"}:
            raise ValueError("unknown calibration status")
        if any(not 0 < q < 1 for q, _ in self.forecast_quantiles):
            raise ValueError("forecast quantile levels must be inside (0, 1)")

    @property
    def costs(self) -> float:
        return self.fees + self.spread + self.slippage + self.impact

    @property
    def net_return(self) -> float:
        return self.gross_return - self.costs

    @property
    def net_alpha(self) -> float:
        return self.net_return - self.benchmark_return

    def utility(self, *, risk_aversion: float = 1.0) -> float:
        return self.net_alpha + self.portfolio_contribution - risk_aversion * self.tail_risk

    def as_dict(self) -> dict:
        return {**asdict(self), "costs": self.costs, "net_return": self.net_return,
                "net_alpha": self.net_alpha, "portfolio_utility": self.utility()}


def rank_opportunities(items: list[OpportunityEstimate], *, min_reliability: float = 0.6,
                       risk_aversion: float = 1.0) -> list[OpportunityEstimate]:
    """Rank only liquid opportunities with an explicit calibrated reliability value."""
    if risk_aversion < 0 or not 0 <= min_reliability <= 1:
        raise ValueError("invalid ranking parameters")
    eligible = [x for x in items if x.liquidity_ok and x.calibration_status == "calibrated"
                and x.reliability is not None
                and x.reliability >= min_reliability]
    return sorted(eligible, key=lambda x: x.utility(risk_aversion=risk_aversion), reverse=True)


def ensemble_forecast(forecasts: dict[str, float], *, validated: set[str],
                      weights: dict[str, float] | None = None) -> dict:
    """Convex ensemble; explicitly abstains if nothing independently validated exists."""
    usable = {k: float(v) for k, v in forecasts.items() if k in validated and np.isfinite(v)}
    if not usable:
        return {"abstain": True, "reason": "no independently validated candidate", "forecast": None,
                "weights": {}}
    raw = {k: (1.0 if weights is None else float(weights.get(k, 0.0))) for k in usable}
    if any(not np.isfinite(v) or v < 0 for v in raw.values()) or sum(raw.values()) <= 0:
        raise ValueError("weights must be finite, nonnegative, and have positive mass")
    total = sum(raw.values())
    normalized = {k: v / total for k, v in raw.items()}
    return {"abstain": False, "reason": None,
            "forecast": sum(usable[k] * normalized[k] for k in usable), "weights": normalized}


def reliability_curve(probabilities: np.ndarray, correct: np.ndarray,
                      *, thresholds: tuple[float, ...] = (0.5, 0.7, 0.9)) -> list[dict]:
    """Selection curve for prior validation observations; not a tuned live gate."""
    p, y = np.asarray(probabilities, float), np.asarray(correct, bool)
    if len(p) != len(y) or len(p) == 0 or not np.isfinite(p).all() or ((p < 0) | (p > 1)).any():
        raise ValueError("invalid reliability observations")
    rows = []
    for threshold in thresholds:
        if not 0 <= threshold <= 1:
            raise ValueError("threshold outside [0, 1]")
        mask = p >= threshold
        rows.append({"threshold": threshold, "n": int(mask.sum()), "coverage": float(mask.mean()),
                     "error_rate": float((~y[mask]).mean()) if mask.any() else None})
    return rows


@dataclass(frozen=True)
class ReliabilityGate:
    """Conservative calibration gate fitted on prior validation labels only."""
    threshold: float
    min_samples: int = 100
    max_brier: float = 0.25

    def assess(self, probabilities: np.ndarray, correct: np.ndarray, candidate_p: float,
               *, asof: datetime | None = None, fitted_through: datetime | None = None,
               out_of_distribution: bool = False) -> dict:
        p, y = np.asarray(probabilities, float), np.asarray(correct, bool)
        if len(p) != len(y) or not np.isfinite(p).all() or not 0 <= candidate_p <= 1:
            raise ValueError("invalid calibration observations")
        if asof is not None and fitted_through is not None and fitted_through >= asof:
            raise ValueError("reliability calibration reaches decision or future time")
        brier = float(np.mean((p - y) ** 2)) if len(p) else None
        reason = ("insufficient calibration sample" if len(p) < self.min_samples else
                  "uncalibrated" if brier is not None and brier > self.max_brier else
                  "out of distribution" if out_of_distribution else
                  "below reliability threshold" if candidate_p < self.threshold else None)
        return {"reliability": candidate_p, "brier": brier, "calibration_n": len(p),
                "abstain": reason is not None, "reason": reason}


def feasible_regret(chosen: dict, alternatives: list[dict]) -> dict:
    """Descriptive hindsight regret; only alternatives feasible at decision time count."""
    required = {"net_return", "feasible_at_decision", "decision_ts"}
    if not required <= chosen.keys() or not chosen["feasible_at_decision"]:
        raise ValueError("chosen decision missing fields or infeasible")
    feasible = [a for a in alternatives if required <= a.keys() and a["feasible_at_decision"]
                and a["decision_ts"] == chosen["decision_ts"] and a.get("known_at_decision", False)]
    best = max([float(chosen["net_return"]), *(float(a["net_return"]) for a in feasible)])
    return {"descriptive_hindsight_regret": best - float(chosen["net_return"]),
            "feasible_alternatives": len(feasible), "unbiased_policy_value": None,
            "warning": "Hindsight best is not an achievable prospective policy estimate."}
