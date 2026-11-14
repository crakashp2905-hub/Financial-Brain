"""Typed, separately evaluated alpha, execution-fill, and risk forecasts.

This module keeps component targets distinct. It reports validation quality; it does not fit a
model, infer a missing target, or convert synthetic metrics into trading authorization.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import json
import math
from typing import Any, Iterable

import numpy as np
import pandas as pd


class ForecastComponentError(ValueError):
    """A component forecast or its independent validation sample is invalid."""


ALPHA_TARGET = "realized_net_alpha_bps_v1"
FILL_TARGET = "filled_by_deadline_binary_v1"
RISK_TARGET = "realized_adverse_excursion_bps_v1"


def _sha(value: str, name: str) -> None:
    if (not isinstance(value, str) or len(value) != 64
            or any(char not in "0123456789abcdef" for char in value)):
        raise ForecastComponentError(f"{name} must be a lowercase SHA-256 digest")


def _object_sha(value: Any) -> str:
    payload = json.dumps(asdict(value), sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ForecastComponentError(f"{name} must be finite numeric")
    return float(value)


@dataclass(frozen=True)
class AlphaForecast:
    model_sha256: str
    expected_net_alpha_bps: float
    horizon_sessions: int
    target_schema: str = ALPHA_TARGET

    def __post_init__(self) -> None:
        _sha(self.model_sha256, "alpha model hash")
        _finite(self.expected_net_alpha_bps, "expected net alpha")
        if type(self.horizon_sessions) is not int or self.horizon_sessions < 1:
            raise ForecastComponentError("alpha horizon must be positive trading sessions")
        if self.target_schema != ALPHA_TARGET:
            raise ForecastComponentError("alpha forecast must use the net-alpha target")


@dataclass(frozen=True)
class FillProbabilityForecast:
    model_sha256: str
    probability: float
    deadline_seconds: int
    target_schema: str = FILL_TARGET

    def __post_init__(self) -> None:
        _sha(self.model_sha256, "execution model hash")
        probability = _finite(self.probability, "fill probability")
        if not 0 <= probability <= 1:
            raise ForecastComponentError("fill probability must be in [0, 1]")
        if type(self.deadline_seconds) is not int or self.deadline_seconds < 1:
            raise ForecastComponentError("fill deadline must be positive seconds")
        if self.target_schema != FILL_TARGET:
            raise ForecastComponentError("fill forecast must use the fill-by-deadline target")


@dataclass(frozen=True)
class RiskForecast:
    model_sha256: str
    loss_quantile_bps: float
    confidence_level: float
    horizon_sessions: int
    target_schema: str = RISK_TARGET

    def __post_init__(self) -> None:
        _sha(self.model_sha256, "risk model hash")
        if _finite(self.loss_quantile_bps, "risk loss quantile") < 0:
            raise ForecastComponentError("risk loss quantile cannot be negative")
        confidence = _finite(self.confidence_level, "risk confidence")
        if not 0 < confidence < 1:
            raise ForecastComponentError("risk confidence must be in (0, 1)")
        if type(self.horizon_sessions) is not int or self.horizon_sessions < 1:
            raise ForecastComponentError("risk horizon must be positive trading sessions")
        if self.target_schema != RISK_TARGET:
            raise ForecastComponentError("risk forecast must use the adverse-excursion target")


@dataclass(frozen=True)
class ForecastBundle:
    """A decision's three independently versioned forecasts with their own target contracts."""

    alpha: AlphaForecast
    fill: FillProbabilityForecast
    risk: RiskForecast

    def as_dict(self) -> dict[str, dict[str, Any]]:
        return {"alpha": asdict(self.alpha), "execution_fill": asdict(self.fill),
                "risk": asdict(self.risk)}


@dataclass(frozen=True)
class AlphaCalibration:
    model_sha256: str
    slope: float
    intercept_bps: float
    development_data_sha256: str
    validation_start: str
    frozen_at_utc: str
    observations: int
    target_schema: str = ALPHA_TARGET

    def apply(self, forecast_bps: float) -> float:
        return self.slope * _finite(forecast_bps, "alpha forecast") + self.intercept_bps

    @property
    def sha256(self) -> str:
        return _object_sha(self)


@dataclass(frozen=True)
class FillCalibration:
    model_sha256: str
    input_thresholds: tuple[float, ...]
    calibrated_rates: tuple[float, ...]
    development_data_sha256: str
    validation_start: str
    frozen_at_utc: str
    observations: int
    target_schema: str = FILL_TARGET

    def apply(self, probability: float) -> float:
        value = _finite(probability, "fill probability")
        if not 0 <= value <= 1:
            raise ForecastComponentError("fill probability must be in [0, 1]")
        return float(np.interp(value, self.input_thresholds, self.calibrated_rates))

    @property
    def sha256(self) -> str:
        return _object_sha(self)


@dataclass(frozen=True)
class RiskCalibration:
    model_sha256: str
    confidence_level: float
    quantile_multiplier: float
    development_data_sha256: str
    validation_start: str
    frozen_at_utc: str
    observations: int
    target_schema: str = RISK_TARGET

    def apply(self, loss_quantile_bps: float) -> float:
        value = _finite(loss_quantile_bps, "risk loss quantile")
        if value < 0:
            raise ForecastComponentError("risk loss quantile cannot be negative")
        return value * self.quantile_multiplier

    @property
    def sha256(self) -> str:
        return _object_sha(self)


def _development_rows(rows: Iterable[dict[str, Any]], *, as_of: datetime,
                      validation_start: datetime, model_sha256: str, target_schema: str,
                      min_observations: int) -> list[dict[str, Any]]:
    _sha(model_sha256, "component model hash")
    if (as_of.tzinfo is None or as_of.utcoffset() is None
            or validation_start.tzinfo is None or validation_start.utcoffset() is None
            or as_of.date() >= validation_start.date()):
        raise ForecastComponentError("calibration must be frozen with an aware timestamp before validation")
    sample = list(rows)
    if len(sample) < min_observations or min_observations < 2:
        raise ForecastComponentError("component development sample is too small")
    cutoff, val_start = pd.Timestamp(as_of).tz_convert("UTC"), pd.Timestamp(validation_start).tz_convert("UTC")
    ids: set[str] = set()
    for row in sample:
        if not isinstance(row, dict) or row.get("split") != "DEVELOPMENT":
            raise ForecastComponentError("component calibration accepts DEVELOPMENT rows only")
        if row.get("model_sha256") != model_sha256 or row.get("target_schema") != target_schema:
            raise ForecastComponentError("component calibration cannot mix model or target identities")
        identity = row.get("decision_sha256")
        if (not isinstance(identity, str) or len(identity) != 64
                or any(c not in "0123456789abcdef" for c in identity) or identity in ids):
            raise ForecastComponentError("development decisions need unique SHA-256 identities")
        ids.add(identity)
        try:
            decision = pd.Timestamp(row["decision_at_utc"])
            maturity = pd.Timestamp(row["target_matured_at_utc"])
            revision = pd.Timestamp(row["target_revised_at_utc"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ForecastComponentError("development row lacks target timing provenance") from exc
        if any(stamp.tzinfo is None for stamp in (decision, maturity, revision)):
            raise ForecastComponentError("component calibration timestamps must be timezone-aware")
        decision, maturity, revision = (stamp.tz_convert("UTC") for stamp in (decision, maturity, revision))
        if (decision >= val_start or maturity < decision or maturity > cutoff
                or revision < maturity or revision > cutoff):
            raise ForecastComponentError("development labels must be mature and revised before the frozen validation cutoff")
    return sample


def _component_data_sha256(rows: list[dict[str, Any]], *, fields: tuple[str, ...]) -> str:
    normalized = []
    for row in rows:
        item: dict[str, Any] = {}
        for key in ("decision_sha256", "target_schema", "model_sha256", "decision_at_utc",
                    "target_matured_at_utc", "target_revised_at_utc", *fields):
            value = row[key]
            if key.endswith("_utc"):
                item[key] = pd.Timestamp(value).tz_convert("UTC").isoformat()
            elif key in {"decision_sha256", "target_schema", "model_sha256"}:
                item[key] = value
            else:
                item[key] = float(value)
        normalized.append(item)
    normalized.sort(key=lambda row: row["decision_sha256"])
    payload = json.dumps(normalized, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def calibrate_alpha_component(rows: Iterable[dict[str, Any]], *, as_of: datetime,
                              validation_start: datetime, model_sha256: str,
                              min_observations: int = 30) -> AlphaCalibration:
    """Fit an affine expected-net-alpha calibration from development rows only."""
    sample = _development_rows(rows, as_of=as_of, validation_start=validation_start,
                               model_sha256=model_sha256, target_schema=ALPHA_TARGET,
                               min_observations=min_observations)
    predicted = np.asarray([_finite(row.get("expected_net_alpha_bps"), "expected net alpha") for row in sample])
    realized = np.asarray([_finite(row.get("realized_net_alpha_bps"), "realized net alpha") for row in sample])
    slope, intercept = np.polyfit(predicted, realized, 1) if np.ptp(predicted) > 0 else (0.0, float(realized.mean()))
    return AlphaCalibration(model_sha256, float(slope), float(intercept),
                            _component_data_sha256(sample, fields=("expected_net_alpha_bps", "realized_net_alpha_bps")),
                            validation_start.date().isoformat(), as_of.isoformat(), len(sample))


def calibrate_fill_component(rows: Iterable[dict[str, Any]], *, as_of: datetime,
                             validation_start: datetime, model_sha256: str,
                             min_observations: int = 30) -> FillCalibration:
    """Fit a monotone pool-adjacent-violators map for fill-by-deadline probabilities."""
    sample = _development_rows(rows, as_of=as_of, validation_start=validation_start,
                               model_sha256=model_sha256, target_schema=FILL_TARGET,
                               min_observations=min_observations)
    predicted = np.asarray([_finite(row.get("fill_probability"), "fill probability") for row in sample])
    raw = [row.get("filled_by_deadline") for row in sample]
    if (any(type(value) not in (bool, int, float) or value not in (0, 1, 0.0, 1.0) for value in raw)
            or ((predicted < 0) | (predicted > 1)).any()
            or len(np.unique(predicted)) < 2):
        raise ForecastComponentError("fill calibration requires binary outcomes and varied valid probabilities")
    outcomes = np.asarray([float(value) for value in raw if isinstance(value, (bool, int, float))])
    grouped: list[list[float]] = []
    for probability in sorted(np.unique(predicted)):
        local_outcomes = np.asarray([float(y) for x, y in zip(predicted, outcomes) if x == probability])
        grouped.append([float(probability), float(local_outcomes.mean()), float(len(local_outcomes))])
    blocks: list[list[float]] = []
    for block in grouped:
        blocks.append(block)
        while len(blocks) >= 2 and blocks[-2][1] > blocks[-1][1]:
            left, right = blocks[-2], blocks[-1]
            weight = left[2] + right[2]
            merged = [(left[0] * left[2] + right[0] * right[2]) / weight,
                      (left[1] * left[2] + right[1] * right[2]) / weight, weight]
            blocks[-2:] = [merged]
    return FillCalibration(model_sha256, tuple(block[0] for block in blocks),
                           tuple(block[1] for block in blocks),
                           _component_data_sha256(sample, fields=("fill_probability", "filled_by_deadline")),
                           validation_start.date().isoformat(), as_of.isoformat(), len(sample))


def calibrate_risk_component(rows: Iterable[dict[str, Any]], *, as_of: datetime,
                             validation_start: datetime, model_sha256: str,
                             confidence_level: float = 0.95,
                             min_observations: int = 30) -> RiskCalibration:
    """Scale a predicted loss quantile to development empirical coverage at its stated level."""
    if not 0 < confidence_level < 1:
        raise ForecastComponentError("risk confidence must be in (0, 1)")
    sample = _development_rows(rows, as_of=as_of, validation_start=validation_start,
                               model_sha256=model_sha256, target_schema=RISK_TARGET,
                               min_observations=min_observations)
    predictions = np.asarray([_finite(row.get("loss_quantile_bps"), "risk loss quantile") for row in sample])
    outcomes = np.asarray([_finite(row.get("realized_adverse_excursion_bps"),
                                   "realized adverse excursion") for row in sample])
    if (predictions <= 0).any() or (outcomes < 0).any():
        raise ForecastComponentError("risk calibration needs positive predicted losses and nonnegative outcomes")
    multiplier = float(np.quantile(outcomes / predictions, confidence_level, method="higher"))
    return RiskCalibration(model_sha256, confidence_level, multiplier,
                           _component_data_sha256(sample, fields=("loss_quantile_bps",
                                                                   "realized_adverse_excursion_bps")),
                           validation_start.date().isoformat(), as_of.isoformat(), len(sample))


def _validation_rows(rows: Iterable[dict[str, Any]], *, as_of: datetime,
                     model_sha256: str, target_schema: str) -> list[dict[str, Any]]:
    _sha(model_sha256, "component model hash")
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ForecastComponentError("validation as_of must be timezone-aware")
    cutoff = pd.Timestamp(as_of).tz_convert("UTC")
    values = list(rows)
    if len(values) < 2:
        raise ForecastComponentError("component validation needs at least two observations")
    ids: set[str] = set()
    for row in values:
        if not isinstance(row, dict) or row.get("split") != "VALIDATION":
            raise ForecastComponentError("component quality metrics accept untouched VALIDATION rows only")
        if row.get("model_sha256") != model_sha256:
            raise ForecastComponentError("validation sample mixes component model versions")
        if row.get("target_schema") != target_schema:
            raise ForecastComponentError("validation sample mixes component target schemas")
        identity = row.get("decision_sha256")
        if (not isinstance(identity, str) or len(identity) != 64
                or any(c not in "0123456789abcdef" for c in identity) or identity in ids):
            raise ForecastComponentError("component validation decisions need unique SHA-256 identities")
        ids.add(identity)
        try:
            decision = pd.Timestamp(row["decision_at_utc"])
            maturity = pd.Timestamp(row["target_matured_at_utc"])
            revision = pd.Timestamp(row["target_revised_at_utc"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ForecastComponentError("validation row lacks decision, target maturity, or revision time") from exc
        if any(stamp.tzinfo is None for stamp in (decision, maturity, revision)):
            raise ForecastComponentError("component validation timestamps must be timezone-aware")
        decision, maturity, revision = (stamp.tz_convert("UTC") for stamp in (decision, maturity, revision))
        if decision > cutoff or maturity > cutoff or revision > cutoff or maturity < decision or revision < maturity:
            raise ForecastComponentError("component target or revision is not known by validation as_of")
    return values


def _reliability_bins(forecast: np.ndarray, observed: np.ndarray, *, bins: int = 10) -> tuple[list[dict[str, float | int]], float]:
    edges = np.linspace(0.0, 1.0, bins + 1)
    reports: list[dict[str, float | int]] = []
    weighted_error = 0.0
    for index, (low, high) in enumerate(zip(edges[:-1], edges[1:])):
        mask = ((forecast >= low) & (forecast <= high)) if index == bins - 1 else (
            (forecast >= low) & (forecast < high))
        if not np.any(mask):
            continue
        predicted, actual = float(forecast[mask].mean()), float(observed[mask].mean())
        count = int(mask.sum())
        reports.append({"lower": float(low), "upper": float(high), "count": count,
                        "mean_forecast": predicted, "observed_rate": actual})
        weighted_error += count * abs(predicted - actual)
    return reports, weighted_error / len(forecast)


def evaluate_alpha_quality(rows: Iterable[dict[str, Any]], *, as_of: datetime,
                           model_sha256: str) -> dict[str, Any]:
    """Assess net-alpha bps forecasts separately from fills and risk outcomes."""
    sample = _validation_rows(rows, as_of=as_of, model_sha256=model_sha256,
                              target_schema=ALPHA_TARGET)
    predicted = np.asarray([_finite(row.get("expected_net_alpha_bps"), "expected net alpha") for row in sample])
    observed = np.asarray([_finite(row.get("realized_net_alpha_bps"), "realized net alpha") for row in sample])
    error = observed - predicted
    slope, intercept = (float(np.polyfit(predicted, observed, 1)[0]),
                        float(np.polyfit(predicted, observed, 1)[1])) if np.ptp(predicted) > 0 else (None, None)
    return {"component": "ALPHA", "target_schema": ALPHA_TARGET, "model_sha256": model_sha256,
            "sample_count": len(sample), "mae_bps": float(np.mean(np.abs(error))),
            "rmse_bps": float(np.sqrt(np.mean(error ** 2))), "bias_bps": float(np.mean(error)),
            "calibration_slope": slope, "calibration_intercept_bps": intercept}


def evaluate_fill_quality(rows: Iterable[dict[str, Any]], *, as_of: datetime,
                          model_sha256: str) -> dict[str, Any]:
    """Assess fill-by-deadline probability using binary outcomes and reliability diagnostics."""
    sample = _validation_rows(rows, as_of=as_of, model_sha256=model_sha256,
                              target_schema=FILL_TARGET)
    predicted = np.asarray([_finite(row.get("fill_probability"), "fill probability") for row in sample])
    raw_observed = [row.get("filled_by_deadline") for row in sample]
    if any(type(value) not in (bool, int, float) or value not in (0, 1, 0.0, 1.0)
           for value in raw_observed):
        raise ForecastComponentError("fill target must be binary and fill probabilities in [0, 1]")
    observed = np.asarray([float(value) for value in raw_observed if isinstance(value, (bool, int, float))])
    if not np.isin(observed, (0.0, 1.0)).all() or ((predicted < 0) | (predicted > 1)).any():
        raise ForecastComponentError("fill target must be binary and fill probabilities in [0, 1]")
    clipped = np.clip(predicted, 1e-12, 1 - 1e-12)
    bins, ece = _reliability_bins(predicted, observed)
    return {"component": "EXECUTION_FILL", "target_schema": FILL_TARGET, "model_sha256": model_sha256,
            "sample_count": len(sample), "brier_score": float(np.mean((predicted - observed) ** 2)),
            "log_loss": float(-np.mean(observed * np.log(clipped) + (1 - observed) * np.log(1 - clipped))),
            "expected_calibration_error": ece, "reliability_bins": bins}


def evaluate_risk_quality(rows: Iterable[dict[str, Any]], *, as_of: datetime,
                          model_sha256: str) -> dict[str, Any]:
    """Assess downside quantile coverage against realized adverse excursion, independently."""
    sample = _validation_rows(rows, as_of=as_of, model_sha256=model_sha256,
                              target_schema=RISK_TARGET)
    forecasts = np.asarray([_finite(row.get("loss_quantile_bps"), "risk loss quantile") for row in sample])
    observed = np.asarray([_finite(row.get("realized_adverse_excursion_bps"),
                                   "realized adverse excursion") for row in sample])
    confidence = np.asarray([_finite(row.get("confidence_level"), "risk confidence") for row in sample])
    if ((forecasts < 0).any() or (observed < 0).any() or ((confidence <= 0) | (confidence >= 1)).any()
            or len(np.unique(confidence)) != 1):
        raise ForecastComponentError("risk losses must be nonnegative and confidence must be a single valid quantile")
    q = float(confidence[0])
    residual = observed - forecasts
    pinball = np.maximum(q * residual, (q - 1) * residual)
    coverage = float(np.mean(observed <= forecasts))
    return {"component": "RISK", "target_schema": RISK_TARGET, "model_sha256": model_sha256,
            "sample_count": len(sample), "confidence_level": q, "empirical_coverage": coverage,
            "coverage_error": coverage - q, "underprediction_rate": float(np.mean(observed > forecasts)),
            "mean_pinball_loss_bps": float(np.mean(pinball))}
