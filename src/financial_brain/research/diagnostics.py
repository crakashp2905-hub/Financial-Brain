"""Causal research diagnostics; none of these metrics proves trading alpha."""
from __future__ import annotations

from math import sqrt

import numpy as np
import pandas as pd


def _wilson(successes: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    if n <= 0:
        return None
    p = successes / n
    den = 1 + z * z / n
    center = (p + z * z / (2 * n)) / den
    half = z * sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, center - half), min(1.0, center + half)


def selective_performance(frame: pd.DataFrame, *, thresholds: tuple[float, ...] = (0.5, 0.7, 0.9)) -> list[dict]:
    """Coverage, error, after-cost return, drawdown and turnover by fixed threshold.

    Input rows must be completed, out-of-sample paper decisions ordered in time.
    These are descriptive; threshold selection must use an earlier validation period.
    """
    required = {"decision_ts", "reliability", "correct", "gross_return", "cost", "turnover"}
    if not required <= set(frame):
        raise ValueError(f"missing fields: {sorted(required - set(frame))}")
    data = frame.sort_values("decision_ts", kind="stable")
    p = data.reliability.to_numpy(float)
    numeric = data[["gross_return", "cost", "turnover"]].to_numpy(float)
    if not np.isfinite(p).all() or ((p < 0) | (p > 1)).any() or not np.isfinite(numeric).all():
        raise ValueError("reliability and performance fields must be finite")
    out = []
    for threshold in thresholds:
        if not 0 <= threshold <= 1:
            raise ValueError("threshold outside [0, 1]")
        part = data.loc[p >= threshold]
        returns = (part.gross_return - part.cost).to_numpy(float)
        wealth = np.cumprod(1 + returns)
        max_drawdown = float(np.min(wealth / np.maximum.accumulate(np.r_[1.0, wealth])[1:] - 1)) if len(wealth) else None
        out.append({"threshold": threshold, "n": len(part), "coverage": len(part) / len(data) if len(data) else 0,
                    "error_rate": float((~part.correct.astype(bool)).mean()) if len(part) else None,
                    "mean_net_return": float(returns.mean()) if len(returns) else None,
                    "max_drawdown": max_drawdown, "total_cost": float(part.cost.sum()),
                    "total_turnover": float(part.turnover.sum()),
                    "warning": "descriptive OOS paper result; do not tune threshold on final holdout"})
    return out


def calibration_report(probability: np.ndarray, success: np.ndarray, *, bins: int = 5) -> dict:
    """Brier score, equal-width reliability bins, and Wilson intervals."""
    p, y = np.asarray(probability, float), np.asarray(success, bool)
    if len(p) != len(y) or len(p) == 0 or bins < 2 or not np.isfinite(p).all() or ((p < 0) | (p > 1)).any():
        raise ValueError("invalid calibration sample")
    rows = []
    for index in range(bins):
        left, right = index / bins, (index + 1) / bins
        mask = (p >= left) & (p <= right if index == bins - 1 else p < right)
        n = int(mask.sum())
        wins = int(y[mask].sum())
        rows.append({"low": left, "high": right, "n": n,
                     "mean_probability": float(p[mask].mean()) if n else None,
                     "observed_success": wins / n if n else None, "wilson_95pct": _wilson(wins, n)})
    return {"n": len(p), "brier": float(np.mean((p - y) ** 2)), "bins": rows}


def regime_transitions(labels: pd.DataFrame, *, min_n: int = 20) -> dict:
    """Transition counts from causal, as-of regime labels in timestamp order."""
    required = {"decision_ts", "regime_asof", "regime"}
    if not required <= set(labels):
        raise ValueError("missing regime fields")
    data = labels.sort_values("decision_ts", kind="stable").copy()
    decision = pd.to_datetime(data.decision_ts, utc=True)
    asof = pd.to_datetime(data.regime_asof, utc=True)
    if (asof > decision).any() or decision.duplicated().any():
        raise ValueError("future or ambiguous regime observation")
    values = data.regime.astype(str).to_numpy()
    counts = {}
    for before, after in zip(values[:-1], values[1:]):
        key = f"{before}->{after}"
        counts[key] = counts.get(key, 0) + 1
    return {"transitions": counts, "n": len(values), "adequate_sample": len(values) >= min_n,
            "warning": "descriptive transition stability, not a causal regime forecast"}


def action_path_error(frame: pd.DataFrame) -> dict:
    """Timing and position reconstruction on explicit observed/predicted paths."""
    required = {"decision_ts", "actual_action", "predicted_action", "actual_position", "predicted_position"}
    if not required <= set(frame):
        raise ValueError("missing action-path fields")
    data = frame.sort_values("decision_ts", kind="stable")
    position_error = np.abs(data.actual_position.to_numpy(float) - data.predicted_position.to_numpy(float))
    if not np.isfinite(position_error).all():
        raise ValueError("invalid positions")
    actual_change = data.actual_action.to_numpy() != "HOLD"
    pred_change = data.predicted_action.to_numpy() != "HOLD"
    actual_indices, pred_indices = np.flatnonzero(actual_change), np.flatnonzero(pred_change)
    timing = (float(np.mean([np.min(np.abs(pred_indices - i)) for i in actual_indices]))
              if len(actual_indices) and len(pred_indices) else None)
    return {"n": len(data), "position_mae": float(position_error.mean()) if len(data) else None,
            "action_timing_error_observations": timing,
            "warning": "timing measured in observation steps, not market minutes"}


class TrainingDistanceGate:
    """Train-frozen robust distance gate for out-of-distribution abstention."""

    def __init__(self, *, quantile: float = .99):
        if not .5 < quantile < 1:
            raise ValueError("quantile must be in (0.5, 1)")
        self.quantile = quantile

    def fit(self, training: np.ndarray) -> "TrainingDistanceGate":
        x = np.asarray(training, float)
        if x.ndim != 2 or len(x) < 50 or not np.isfinite(x).all():
            raise ValueError("at least 50 finite training observations required")
        self.center = np.median(x, axis=0)
        self.scale = np.maximum(np.median(np.abs(x - self.center), axis=0) * 1.4826, 1e-6)
        self.threshold = float(np.quantile(self._distance(x), self.quantile))
        return self

    def _distance(self, x: np.ndarray) -> np.ndarray:
        return np.sqrt(np.mean(((x - self.center) / self.scale) ** 2, axis=1))

    def assess(self, observations: np.ndarray) -> dict:
        if not hasattr(self, "threshold"):
            raise ValueError("fit on a prior training period first")
        x = np.asarray(observations, float)
        if x.ndim != 2 or x.shape[1] != len(self.center) or not np.isfinite(x).all():
            raise ValueError("invalid observations")
        distances = self._distance(x)
        flags = distances > self.threshold
        return {"distances": distances, "ood": flags, "ood_fraction": float(flags.mean()) if len(flags) else None,
                "threshold": self.threshold,
                "warning": "OOD is a feature-distance flag, not a calibrated failure probability"}
