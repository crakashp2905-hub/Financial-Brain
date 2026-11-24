"""Diagnostic chart-pattern uplift after known factor and session controls (FE-0083)."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date

import numpy as np


class PatternUpliftError(ValueError):
    """Insufficient or invalid factor-controlled evaluation data."""


@dataclass(frozen=True)
class PatternObservation:
    session: date
    instrument_id: str
    pattern_signal: bool
    momentum_12_1: float
    distance_from_52w_high: float
    volatility: float
    after_cost_net_return: float

    def __post_init__(self) -> None:
        if not self.instrument_id.strip():
            raise PatternUpliftError("instrument_id is required")
        for name, value in (("momentum_12_1", self.momentum_12_1),
                            ("distance_from_52w_high", self.distance_from_52w_high),
                            ("volatility", self.volatility),
                            ("after_cost_net_return", self.after_cost_net_return)):
            if not math.isfinite(float(value)):
                raise PatternUpliftError(f"{name} must be finite")
        if self.volatility < 0:
            raise PatternUpliftError("volatility cannot be negative")


@dataclass(frozen=True)
class PatternUpliftReport:
    observations: int
    pattern_observations: int
    control_observations: int
    raw_mean_difference: float
    factor_adjusted_pattern_coefficient: float
    controls: tuple[str, ...]
    inference_status: str
    report_hash: str


def estimate_pattern_uplift(observations: tuple[PatternObservation, ...]) -> PatternUpliftReport:
    """Estimate the pattern coefficient with fixed session effects and three known factors.

    The output is descriptive. It does not correct for strategy search multiplicity,
    dependence across instruments/dates, or preregistration; it cannot promote a strategy.
    """
    if len(observations) < 12:
        raise PatternUpliftError("at least 12 observations are required for this diagnostic")
    ids = [(item.session, item.instrument_id) for item in observations]
    if len(ids) != len(set(ids)):
        raise PatternUpliftError("duplicate instrument/session observation")
    labels = np.asarray([int(item.pattern_signal) for item in observations], dtype=float)
    if len(set(labels.tolist())) != 2:
        raise PatternUpliftError("both pattern and non-pattern observations are required")
    sessions = sorted({item.session for item in observations})
    factor_columns = []
    for attr in ("momentum_12_1", "distance_from_52w_high", "volatility"):
        values = np.asarray([getattr(item, attr) for item in observations], dtype=float)
        std = float(values.std())
        if std == 0.0:
            raise PatternUpliftError(f"control {attr} has no variation")
        factor_columns.append((values - float(values.mean())) / std)
    # Fixed session intercepts control common market direction on each evaluation date.
    date_columns = [np.asarray([float(item.session == session) for item in observations])
                    for session in sessions[1:]]
    design = np.column_stack([np.ones(len(observations)), labels, *factor_columns, *date_columns])
    if len(observations) <= design.shape[1] or np.linalg.matrix_rank(design) != design.shape[1]:
        raise PatternUpliftError("factor/session design is underdetermined or collinear")
    y = np.asarray([item.after_cost_net_return for item in observations], dtype=float)
    coefficients, *_ = np.linalg.lstsq(design, y, rcond=None)
    raw = float(y[labels == 1].mean() - y[labels == 0].mean())
    adjusted = float(coefficients[1])
    controls = ("momentum_12_1", "distance_from_52w_high", "volatility", "session_fixed_effects")
    fields = {"observations": len(observations), "pattern_observations": int(labels.sum()),
              "control_observations": int((labels == 0).sum()), "raw_mean_difference": raw,
              "factor_adjusted_pattern_coefficient": adjusted, "controls": list(controls),
              "inference_status": "DESCRIPTIVE_ONLY_DEPENDENCE_AND_MULTIPLICITY_UNADJUSTED"}
    digest = hashlib.sha256(json.dumps(fields, sort_keys=True, separators=(",", ":"),
                                      allow_nan=False).encode()).hexdigest()
    return PatternUpliftReport(len(observations), int(labels.sum()), int((labels == 0).sum()),
                               raw, adjusted, controls,
                               "DESCRIPTIVE_ONLY_DEPENDENCE_AND_MULTIPLICITY_UNADJUSTED", digest)
