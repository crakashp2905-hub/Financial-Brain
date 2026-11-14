"""Development-calibrated forecast benefit haircuts and cost-to-benefit abstention."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

from .durable import DurableJournal


class CostBenefitError(ValueError):
    """Invalid benefit calibration, frozen identity, or decision forecast."""


def _digest(value: object, label: str) -> str:
    if (not isinstance(value, str) or len(value) != 64
            or any(char not in "0123456789abcdef" for char in value)):
        raise CostBenefitError(f"{label} must be a lowercase SHA-256 digest")
    return value


@dataclass(frozen=True)
class ForecastBenefitPolicy:
    strategy_sha256: str
    cohort_id: str
    forecast_model_sha256: str
    conservative_benefit_scale: float
    max_cost_to_benefit_ratio: float
    development_data_sha256: str
    calibration_protocol_sha256: str
    development_start: str
    development_end: str
    validation_start: str
    observation_count: int
    frozen_at_utc: str

    def __post_init__(self) -> None:
        for field, label in ((self.strategy_sha256, "strategy hash"),
                             (self.forecast_model_sha256, "forecast model hash"),
                             (self.development_data_sha256, "development data hash"),
                             (self.calibration_protocol_sha256, "calibration protocol hash")):
            _digest(field, label)
        if not self.cohort_id.strip():
            raise CostBenefitError("cohort id is required")
        if (not math.isfinite(self.conservative_benefit_scale)
                or not 0 <= self.conservative_benefit_scale <= 1
                or not math.isfinite(self.max_cost_to_benefit_ratio)
                or not 0 <= self.max_cost_to_benefit_ratio < 1):
            raise CostBenefitError("benefit scale must be in [0, 1] and cost ratio limit in [0, 1)")
        if isinstance(self.observation_count, bool) or self.observation_count < 2:
            raise CostBenefitError("calibration needs at least two development observations")
        try:
            start = date.fromisoformat(self.development_start)
            end = date.fromisoformat(self.development_end)
            validation = date.fromisoformat(self.validation_start)
            frozen = datetime.fromisoformat(self.frozen_at_utc)
        except (TypeError, ValueError) as exc:
            raise CostBenefitError("calibration dates and freeze timestamp must be canonical") from exc
        if start > end or end >= validation:
            raise CostBenefitError("development data must end before validation starts")
        if frozen.tzinfo is None or frozen.utcoffset() is None or frozen.date() >= validation:
            raise CostBenefitError("policy must be frozen with an aware timestamp before validation")

    @property
    def sha256(self) -> str:
        body = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(body.encode("utf-8")).hexdigest()


def calibrate_forecast_benefit_policy(*, strategy_sha256: str, cohort_id: str,
                                      forecast_model_sha256: str,
                                      observations: Iterable[dict[str, object]],
                                      validation_start: date, frozen_at: datetime,
                                      calibration_protocol_sha256: str,
                                      max_cost_to_benefit_ratio: float = 0.90,
                                      min_observations: int = 30) -> ForecastBenefitPolicy:
    """Fit a through-origin forecast benefit scale on positive DEVELOPMENT forecasts only.

    The fitted scale is clipped to [0,1], so development calibration cannot inflate benefit.
    It is a conservative shrinkage diagnostic and not proof of calibrated profitability.
    """
    for value, label in ((strategy_sha256, "strategy hash"),
                         (forecast_model_sha256, "forecast model hash"),
                         (calibration_protocol_sha256, "calibration protocol hash")):
        _digest(value, label)
    if not cohort_id.strip() or not 0 <= max_cost_to_benefit_ratio < 1:
        raise CostBenefitError("cohort and cost-to-benefit limit below one are required")
    rows = list(observations)
    if isinstance(min_observations, bool) or min_observations < 2 or len(rows) < min_observations:
        raise CostBenefitError("development calibration sample is too small")
    normalized: list[dict[str, object]] = []
    dates: list[date] = []
    identities: set[str] = set()
    pairs: list[tuple[float, float]] = []
    for row in rows:
        if not isinstance(row, dict) or row.get("split") != "DEVELOPMENT":
            raise CostBenefitError("benefit calibration accepts DEVELOPMENT rows only")
        try:
            day = date.fromisoformat(str(row["exchange_date"]))
            forecast = row["forecast_benefit"]
            realized = row["realized_excess"]
            decision_sha = row["decision_sha256"]
            model_sha = row["forecast_model_sha256"]
            if (isinstance(forecast, bool) or not isinstance(forecast, (int, float))
                    or isinstance(realized, bool) or not isinstance(realized, (int, float))):
                raise TypeError
            forecast, realized = float(forecast), float(realized)
        except (KeyError, TypeError, ValueError) as exc:
            raise CostBenefitError("development row lacks forecast, realized excess, date, or identity") from exc
        if (day >= validation_start or not math.isfinite(forecast) or forecast <= 0
                or not math.isfinite(realized) or model_sha != forecast_model_sha256):
            raise CostBenefitError("development forecasts must be positive, finite, model-matched and pre-validation")
        if (not isinstance(decision_sha, str) or len(decision_sha) != 64
                or any(char not in "0123456789abcdef" for char in decision_sha)
                or decision_sha in identities):
            raise CostBenefitError("development decision identities must be unique SHA-256 digests")
        identities.add(decision_sha)
        dates.append(day)
        pairs.append((forecast, realized))
        normalized.append({"exchange_date": day.isoformat(), "split": "DEVELOPMENT",
                           "forecast_benefit": forecast, "realized_excess": realized,
                           "decision_sha256": decision_sha, "forecast_model_sha256": model_sha})
    denominator = sum(forecast * forecast for forecast, _ in pairs)
    scale = min(1.0, max(0.0, sum(forecast * realized for forecast, realized in pairs) / denominator))
    normalized.sort(key=lambda row: (str(row["exchange_date"]), str(row["decision_sha256"])))
    data_sha = hashlib.sha256(json.dumps(normalized, sort_keys=True, separators=(",", ":"),
                                         allow_nan=False).encode("utf-8")).hexdigest()
    return ForecastBenefitPolicy(
        strategy_sha256=strategy_sha256, cohort_id=cohort_id, forecast_model_sha256=forecast_model_sha256,
        conservative_benefit_scale=scale, max_cost_to_benefit_ratio=max_cost_to_benefit_ratio,
        development_data_sha256=data_sha, calibration_protocol_sha256=calibration_protocol_sha256,
        development_start=min(dates).isoformat(), development_end=max(dates).isoformat(),
        validation_start=validation_start.isoformat(), observation_count=len(rows),
        frozen_at_utc=frozen_at.isoformat())


def assess_cost_to_benefit(*, forecast_benefit: float, fees: float, spread: float, impact: float,
                           forecast_model_sha256: str, session_date: date,
                           policy: ForecastBenefitPolicy | None,
                           strategy_sha256: str, cohort_id: str) -> dict[str, object]:
    """Fail closed unless a matching frozen policy says conservative forecast benefit clears costs."""
    values = (forecast_benefit, fees, spread, impact)
    if not all(math.isfinite(value) for value in values) or min(fees, spread, impact) < 0:
        raise CostBenefitError("forecast benefit and itemized costs must be finite; costs non-negative")
    if policy is None:
        return {"approved": False, "reason": "FORECAST_BENEFIT_UNCALIBRATED", "ratio": None,
                "calibrated_benefit": None, "itemized_costs": fees + spread + impact}
    identity_matches = (policy.strategy_sha256 == strategy_sha256 and policy.cohort_id == cohort_id
                        and policy.forecast_model_sha256 == forecast_model_sha256)
    if not identity_matches or session_date < date.fromisoformat(policy.validation_start):
        return {"approved": False, "reason": "FORECAST_BENEFIT_POLICY_MISMATCH", "ratio": None,
                "calibrated_benefit": None, "itemized_costs": fees + spread + impact,
                "policy_sha256": policy.sha256}
    benefit = forecast_benefit * policy.conservative_benefit_scale
    costs = fees + spread + impact
    ratio = costs / benefit if benefit > 0 else None
    approved = ratio is not None and ratio <= policy.max_cost_to_benefit_ratio
    return {"approved": approved,
            "reason": "COST_BENEFIT_WITHIN_FROZEN_LIMIT" if approved else "COST_EXCEEDS_CALIBRATED_BENEFIT",
            "forecast_benefit": forecast_benefit, "calibrated_benefit": benefit,
            "itemized_costs": costs, "ratio": ratio,
            "max_cost_to_benefit_ratio": policy.max_cost_to_benefit_ratio,
            "forecast_model_sha256": forecast_model_sha256, "policy_sha256": policy.sha256}


class ForecastBenefitPolicyRegistry:
    """Append-only, conflict-detecting policy store by strategy and cohort."""

    def __init__(self, path: Path) -> None:
        self.journal = DurableJournal(path)

    def freeze(self, policy: ForecastBenefitPolicy) -> str:
        prior = self.resolve(policy.strategy_sha256, policy.cohort_id)
        if prior is not None:
            if prior.sha256 != policy.sha256:
                raise CostBenefitError("frozen forecast-benefit policy cannot be replaced")
            return prior.sha256
        self.journal.append({"t": "FORECAST_BENEFIT_POLICY_FROZEN", "policy": asdict(policy),
                             "policy_sha256": policy.sha256})
        return policy.sha256

    def resolve(self, strategy_sha256: str, cohort_id: str) -> ForecastBenefitPolicy | None:
        rows = [row for row in self.journal.records()
                if row.get("t") == "FORECAST_BENEFIT_POLICY_FROZEN"
                and row.get("policy", {}).get("strategy_sha256") == strategy_sha256
                and row.get("policy", {}).get("cohort_id") == cohort_id]
        if not rows:
            return None
        first = rows[0]
        if any(row != first for row in rows[1:]):
            raise CostBenefitError("registry contains conflicting frozen forecast-benefit policies")
        policy = ForecastBenefitPolicy(**first["policy"])
        if policy.sha256 != first.get("policy_sha256"):
            raise CostBenefitError("forecast-benefit policy digest mismatch")
        return policy
