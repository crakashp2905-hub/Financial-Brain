"""Leakage-guarded comparison of ensemble weighting policies.

Inputs must be predictions from independently validated strategies. Outcomes are
used only after their timestamp. The output is a research comparison, never a
promotion decision.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.optimize import minimize


def _simplex_fit(x: np.ndarray, y: np.ndarray, ridge: float) -> np.ndarray:
    n = x.shape[1]
    equal = np.full(n, 1 / n)
    result = minimize(lambda w: float(np.mean((x @ w - y) ** 2) + ridge * ((w - equal) ** 2).sum()),
                      equal, method="SLSQP", bounds=[(0, 1)] * n,
                      constraints={"type": "eq", "fun": lambda w: w.sum() - 1},
                      options={"ftol": 1e-12, "maxiter": 100})
    if not result.success:
        return equal
    weights = np.maximum(result.x, 0)
    return weights / weights.sum()


def compare_ensembles(panel: pd.DataFrame, strategies: tuple[str, ...], *, validated: set[str],
                      min_train: int = 60, window: int = 250, ridge: float = 0.1,
                      online_decay: float = 0.97) -> pd.DataFrame:
    """Equal, static, regime-conditioned, and prequential adaptive forecasts.

    ``observed_at`` is when the outcome became available, not just its market date.
    Every training row must have ``observed_at < decision_ts`` at prediction time.
    """
    required = {"decision_ts", "observed_at", "regime", "realized", *strategies}
    if not required <= set(panel) or not strategies or not set(strategies) <= validated:
        raise ValueError("missing fields or unvalidated strategies")
    if min_train < 10 or window < min_train or ridge < 0 or not 0 < online_decay < 1:
        raise ValueError("invalid ensemble configuration")
    data = panel.sort_values("decision_ts", kind="stable").reset_index(drop=True).copy()
    data["decision_ts"] = pd.to_datetime(data.decision_ts, utc=True)
    data["observed_at"] = pd.to_datetime(data.observed_at, utc=True)
    if (data.observed_at < data.decision_ts).any() or data.decision_ts.duplicated().any():
        raise ValueError("outcomes must follow unique decision timestamps")
    cols = list(strategies)
    if not np.isfinite(data[[*cols, "realized"]].to_numpy(float)).all():
        raise ValueError("forecasts and outcomes must be finite")
    x, y = data[cols].to_numpy(float), data.realized.to_numpy(float)
    rows = []
    equal = np.full(len(cols), 1 / len(cols))
    for i in range(len(data)):
        current = data.decision_ts.iloc[i]
        eligible = np.flatnonzero((data.observed_at < current).to_numpy() &
                                  (np.arange(len(data)) < i))
        eligible = eligible[-window:]
        if len(eligible) < min_train:
            continue
        xs, ys = x[eligible], y[eligible]
        static = _simplex_fit(xs, ys, ridge)
        same = eligible[data.regime.iloc[eligible].to_numpy() == data.regime.iloc[i]]
        regime = _simplex_fit(x[same], y[same], ridge) if len(same) >= min_train else equal
        errors = (xs - ys[:, None]) ** 2
        age_weights = online_decay ** np.arange(len(eligible) - 1, -1, -1)
        loss = np.average(errors, axis=0, weights=age_weights)
        scale = max(float(np.median(loss)), 1e-8)
        adaptive = np.exp(-loss / scale)
        adaptive /= adaptive.sum()
        for name, weights in (("equal", equal), ("static", static), ("regime", regime), ("online", adaptive)):
            rows.append({"decision_ts": current, "policy": name, "forecast": float(x[i] @ weights),
                         "realized": float(y[i]), "training_n": len(eligible),
                         "training_observed_through": data.observed_at.iloc[eligible].max(),
                         "weights": dict(zip(cols, weights.tolist()))})
    return pd.DataFrame(rows)
