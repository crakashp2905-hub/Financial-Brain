"""Development-only factor and turnover regularization for candidate comparisons."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Any, Iterable


class FactorRegularizationError(ValueError):
    """Candidate data or frozen regularization policy is invalid."""


REQUIRED_FACTORS = ("size", "momentum", "beta")


@dataclass(frozen=True)
class RegularizationPolicy:
    """Frozen penalty per unit of factor exposure and round-trip turnover."""

    factor_penalty_bps_per_unit: dict[str, float]
    turnover_cost_bps_per_unit: float
    version: str

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise FactorRegularizationError("regularization policy version is required")
        if set(self.factor_penalty_bps_per_unit) != set(REQUIRED_FACTORS):
            raise FactorRegularizationError("policy must define size, momentum, and beta penalties")
        values = [*self.factor_penalty_bps_per_unit.values(), self.turnover_cost_bps_per_unit]
        if any(not math.isfinite(value) or value < 0 for value in values):
            raise FactorRegularizationError("regularization penalty values must be finite and nonnegative")

    @property
    def sha256(self) -> str:
        body = {"version": self.version,
                "factor_penalty_bps_per_unit": self.factor_penalty_bps_per_unit,
                "turnover_cost_bps_per_unit": self.turnover_cost_bps_per_unit}
        return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                        allow_nan=False).encode()).hexdigest()


def compare_candidates(candidates: Iterable[dict[str, Any]], *, policy: RegularizationPolicy,
                       data_split: str) -> dict[str, Any]:
    """Rank declared candidates after explicit factor and turnover regularization.

    Inputs are diagnostics only: validation/test data are rejected, no model is fitted, and
    regularized forecasts cannot authorize a paper or live order.
    """
    if data_split != "DEVELOPMENT":
        raise FactorRegularizationError("factor/turnover regularization is development-only")
    rows = list(candidates)
    if not rows:
        raise FactorRegularizationError("at least one candidate is required")
    ids: set[str] = set()
    scored: list[dict[str, Any]] = []
    for row in rows:
        candidate_id = row.get("candidate_id")
        if not isinstance(candidate_id, str) or not candidate_id.strip() or candidate_id in ids:
            raise FactorRegularizationError("candidate IDs must be present and unique")
        ids.add(candidate_id)
        try:
            raw_alpha = float(row["forecast_net_alpha_bps"])
            turnover = float(row["expected_turnover_per_session"])
            exposures = {factor: float(row["factor_exposures"][factor]) for factor in REQUIRED_FACTORS}
        except (KeyError, TypeError, ValueError) as exc:
            raise FactorRegularizationError("candidate needs alpha, turnover, and size/momentum/beta exposures") from exc
        values = [raw_alpha, turnover, *exposures.values()]
        if any(not math.isfinite(value) for value in values) or turnover < 0:
            raise FactorRegularizationError("candidate estimates must be finite and turnover nonnegative")
        factor_drag = sum(abs(exposures[factor]) * policy.factor_penalty_bps_per_unit[factor]
                          for factor in REQUIRED_FACTORS)
        turnover_drag = turnover * policy.turnover_cost_bps_per_unit
        adjusted = raw_alpha - factor_drag - turnover_drag
        scored.append({
            "candidate_id": candidate_id,
            "forecast_net_alpha_bps": raw_alpha,
            "factor_exposures": exposures,
            "factor_regularization_drag_bps": factor_drag,
            "expected_turnover_per_session": turnover,
            "turnover_regularization_drag_bps": turnover_drag,
            "regularized_forecast_alpha_bps": adjusted,
            "factor_or_turnover_explains_forecast": adjusted <= 0 < raw_alpha,
            "decision_diagnostic": "POTENTIAL_FACTOR_OR_TURNOVER_EXPLANATION" if adjusted <= 0 < raw_alpha
            else "DEVELOPMENT_DIAGNOSTIC_ONLY",
        })
    scored.sort(key=lambda item: (-item["regularized_forecast_alpha_bps"], item["candidate_id"]))
    return {
        "schema_version": "fb.factor-turnover-comparison.v1",
        "data_split": data_split,
        "regularization_policy_sha256": policy.sha256,
        "candidate_count": len(scored),
        "candidates": scored,
        "promotion_eligible": False,
        "interpretation": "Development forecast diagnostic; no validation, confirmation, or order authorization.",
    }
