"""Development-calibrated uncertainty haircuts for expected-alpha sizing."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

from .durable import DurableJournal


class UncertaintySizingError(ValueError):
    """An uncertainty calibration is invalid, unfrozen, or out of sample."""


def _finite_number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("numeric calibration value required")
    return float(value)


@dataclass(frozen=True)
class UncertaintySizingPolicy:
    strategy_sha256: str
    cohort_id: str
    standardized_error_multiplier: float
    confidence_z: float
    development_data_sha256: str
    calibration_protocol_sha256: str
    development_start: str
    development_end: str
    validation_start: str
    observation_count: int
    frozen_at_utc: str

    def __post_init__(self) -> None:
        for name in ("strategy_sha256", "development_data_sha256", "calibration_protocol_sha256"):
            value = getattr(self, name)
            if (not isinstance(value, str) or len(value) != 64
                    or any(char not in "0123456789abcdef" for char in value)):
                raise UncertaintySizingError(f"{name} must be a SHA-256 digest")
        if not isinstance(self.cohort_id, str) or not self.cohort_id.strip():
            raise UncertaintySizingError("cohort id is required")
        if (not math.isfinite(self.standardized_error_multiplier)
                or self.standardized_error_multiplier < 1
                or not math.isfinite(self.confidence_z) or self.confidence_z <= 0):
            raise UncertaintySizingError("uncertainty multiplier must be >= 1 and confidence z positive")
        if isinstance(self.observation_count, bool) or self.observation_count < 2:
            raise UncertaintySizingError("uncertainty calibration requires at least two observations")
        try:
            start = date.fromisoformat(self.development_start)
            end = date.fromisoformat(self.development_end)
            validation = date.fromisoformat(self.validation_start)
            frozen = datetime.fromisoformat(self.frozen_at_utc)
        except (TypeError, ValueError) as exc:
            raise UncertaintySizingError("calibration dates and freeze timestamp must be canonical") from exc
        if start > end or end >= validation:
            raise UncertaintySizingError("development data must end before validation starts")
        if frozen.tzinfo is None or frozen.utcoffset() is None or frozen.date() >= validation:
            raise UncertaintySizingError("policy must be frozen with an aware timestamp before validation")

    def as_dict(self) -> dict[str, object]:
        return asdict(self)

    @property
    def sha256(self) -> str:
        body = json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(body.encode("utf-8")).hexdigest()


def calibrate_uncertainty_policy(*, strategy_sha256: str, cohort_id: str,
                                 observations: Iterable[dict[str, object]],
                                 validation_start: date, frozen_at: datetime,
                                 calibration_protocol_sha256: str,
                                 confidence_z: float = 1.645, min_observations: int = 30,
                                 coverage_quantile: float = 0.90) -> UncertaintySizingPolicy:
    """Inflate stated standard errors to a predeclared empirical residual quantile.

    Calibration accepts development rows only and never deflates reported uncertainty: the
    multiplier is at least one. This calibrates a conservative sizing haircut, not profitability.
    """
    rows = list(observations)
    if isinstance(min_observations, bool) or min_observations < 2 or len(rows) < min_observations:
        raise UncertaintySizingError("development uncertainty sample is too small")
    if (not math.isfinite(confidence_z) or confidence_z <= 0
            or not 0 < coverage_quantile < 1):
        raise UncertaintySizingError("confidence z and coverage quantile must be valid")
    standardized_errors: list[float] = []
    dates: list[date] = []
    identities: set[str] = set()
    normalized: list[dict[str, object]] = []
    for row in rows:
        if not isinstance(row, dict) or row.get("split") != "DEVELOPMENT":
            raise UncertaintySizingError("uncertainty calibration may consume DEVELOPMENT rows only")
        try:
            observed_value = row["exchange_date"]
            forecast_value = _finite_number(row["forecast_net_alpha"])
            uncertainty_value = _finite_number(row["uncertainty_standard_error"])
            realized_value = _finite_number(row["realized_net_alpha"])
            identity = row["decision_sha256"]
            if not isinstance(observed_value, str):
                raise TypeError
            observed = date.fromisoformat(observed_value)
            forecast = forecast_value
            uncertainty = uncertainty_value
            realized = realized_value
        except (KeyError, TypeError, ValueError) as exc:
            raise UncertaintySizingError("development row lacks typed forecast/outcome provenance") from exc
        if (observed >= validation_start or not all(map(math.isfinite, (forecast, uncertainty, realized)))
                or uncertainty <= 0):
            raise UncertaintySizingError("development rows must precede validation and have finite positive uncertainty")
        if (not isinstance(identity, str) or len(identity) != 64
                or any(char not in "0123456789abcdef" for char in identity) or identity in identities):
            raise UncertaintySizingError("development decision identities must be unique SHA-256 digests")
        identities.add(identity)
        dates.append(observed)
        standardized_errors.append(abs(realized - forecast) / uncertainty)
        normalized.append({"exchange_date": observed.isoformat(), "split": "DEVELOPMENT",
                           "forecast_net_alpha": forecast, "uncertainty_standard_error": uncertainty,
                           "realized_net_alpha": realized, "decision_sha256": identity})
    normalized.sort(key=lambda item: (str(item["exchange_date"]), str(item["decision_sha256"])))
    standardized_errors.sort()
    rank = max(0, math.ceil(coverage_quantile * len(standardized_errors)) - 1)
    multiplier = max(1.0, standardized_errors[rank])
    data_body = json.dumps(normalized, sort_keys=True, separators=(",", ":"), allow_nan=False)
    data_sha = hashlib.sha256(data_body.encode("utf-8")).hexdigest()
    return UncertaintySizingPolicy(
        strategy_sha256=strategy_sha256, cohort_id=cohort_id,
        standardized_error_multiplier=multiplier, confidence_z=confidence_z,
        development_data_sha256=data_sha, calibration_protocol_sha256=calibration_protocol_sha256,
        development_start=min(dates).isoformat(), development_end=max(dates).isoformat(),
        validation_start=validation_start.isoformat(), observation_count=len(rows),
        frozen_at_utc=frozen_at.isoformat())


def uncertainty_size_multiplier(*, expected_net_alpha: float, uncertainty_standard_error: float,
                                policy: UncertaintySizingPolicy) -> float:
    """Return a bounded [0, 1] size multiplier; greater uncertainty cannot increase size."""
    if not math.isfinite(expected_net_alpha) or not math.isfinite(uncertainty_standard_error) \
            or uncertainty_standard_error < 0:
        raise UncertaintySizingError("alpha must be finite and uncertainty must be finite and non-negative")
    magnitude = abs(expected_net_alpha)
    if magnitude == 0:
        return 0.0
    margin = policy.confidence_z * policy.standardized_error_multiplier * uncertainty_standard_error
    return max(0.0, min(1.0, 1.0 - margin / magnitude))


class UncertaintySizingRegistry:
    """Hash-chained registry for immutable strategy/cohort uncertainty policies."""

    def __init__(self, path: Path) -> None:
        self.journal = DurableJournal(path)

    def freeze(self, policy: UncertaintySizingPolicy) -> str:
        prior = self.resolve(policy.strategy_sha256, policy.cohort_id)
        if prior is not None:
            if prior.sha256 != policy.sha256:
                raise UncertaintySizingError("frozen cohort uncertainty policy cannot be changed")
            return prior.sha256
        self.journal.append({"t": "UNCERTAINTY_SIZING_POLICY_FROZEN", "policy": policy.as_dict(),
                             "policy_sha256": policy.sha256})
        return policy.sha256

    def resolve(self, strategy_sha256: str, cohort_id: str) -> UncertaintySizingPolicy | None:
        rows = [row for row in self.journal.records()
                if row.get("t") == "UNCERTAINTY_SIZING_POLICY_FROZEN"
                and row.get("policy", {}).get("strategy_sha256") == strategy_sha256
                and row.get("policy", {}).get("cohort_id") == cohort_id]
        if not rows:
            return None
        first = rows[0]
        if any(row != first for row in rows[1:]):
            raise UncertaintySizingError("uncertainty registry contains conflicting frozen policies")
        policy = UncertaintySizingPolicy(**first["policy"])
        if policy.sha256 != first.get("policy_sha256"):
            raise UncertaintySizingError("frozen uncertainty policy digest mismatch")
        return policy
