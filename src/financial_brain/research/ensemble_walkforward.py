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

    ``estimate_asof`` is when forecasts and ex-ante cost/benchmark estimates were
    available. ``observed_at`` is when the outcome became available, not just its market date.
    Every training row must have ``observed_at < decision_ts`` at prediction time.
    """
    required = {"decision_ts", "estimate_asof", "observed_at", "regime", "realized", *strategies}
    if not required <= set(panel) or not strategies or not set(strategies) <= validated:
        raise ValueError("missing fields or unvalidated strategies")
    if min_train < 10 or window < min_train or ridge < 0 or not 0 < online_decay < 1:
        raise ValueError("invalid ensemble configuration")
    data = panel.sort_values("decision_ts", kind="stable").reset_index(drop=True).copy()
    for column in ("decision_ts", "estimate_asof", "observed_at"):
        if data[column].isna().any() or not data[column].map(
                lambda value: pd.Timestamp(value).tzinfo is not None).all():
            raise ValueError(f"{column} requires an explicit timezone")
        data[column] = pd.to_datetime(data[column], utc=True)
    if (data.estimate_asof > data.decision_ts).any() or (data.observed_at < data.decision_ts).any():
        raise ValueError("future estimate or outcome before decision")
    if data.decision_ts.duplicated().any():
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


def evaluate_ensemble_paper(panel: pd.DataFrame, strategies: tuple[str, ...], *, validated: set[str],
                            min_train: int = 60, window: int = 250,
                            min_expected_alpha: float = 0.0) -> dict:
    """Fixed-threshold, long-only after-cost evaluation of prequential forecasts.

    Required `cost_expected` and `benchmark_expected` are decision-time
    estimates; `cost_realized`, `turnover`, `benchmark_realized`, and `realized`
    are observed later. No
    threshold is selected here.
    Each row is one nonoverlapping, chronological decision opportunity.
    """
    fields = {"cost_expected", "cost_realized", "benchmark_expected", "benchmark_realized", "turnover"}
    if not fields <= set(panel):
        raise ValueError("expected/realized cost and benchmark, and turnover fields required")
    if not np.isfinite(min_expected_alpha) or (panel[["cost_expected", "cost_realized", "turnover"]] < 0).any().any():
        raise ValueError("invalid threshold, cost, or turnover")
    if not np.isfinite(panel[list(fields)].to_numpy(float)).all():
        raise ValueError("cost, expected/realized benchmark, and turnover must be finite")
    if (panel.realized - panel.cost_realized <= -1).any():
        raise ValueError("after-cost return must be greater than -100%")
    rows = compare_ensembles(panel, strategies, validated=validated,
                             min_train=min_train, window=window)
    if rows.empty:
        return {"status": "insufficient_completed_history", "policies": {}}
    asof = panel.copy()
    asof["decision_ts"] = pd.to_datetime(asof.decision_ts, utc=True)
    rows = rows.merge(asof[["decision_ts", "cost_expected", "cost_realized", "benchmark_expected",
                            "benchmark_realized", "turnover"]],
                      on="decision_ts", validate="many_to_one")
    rows["trade"] = rows.forecast - rows.benchmark_expected - rows.cost_expected > min_expected_alpha
    rows["net_return"] = np.where(rows.trade, rows.realized - rows.cost_realized, 0.0)
    rows["net_alpha"] = rows.net_return - rows.benchmark_realized
    output = {}
    for policy, part in rows.groupby("policy", sort=True):
        ordered = part.sort_values("decision_ts")
        wealth = np.cumprod(1 + ordered.net_return.to_numpy(float))
        peak = np.maximum.accumulate(np.r_[1.0, wealth])[1:]
        output[policy] = {"n": len(ordered), "trades": int(ordered.trade.sum()),
                          "coverage": float(ordered.trade.mean()),
                          "mean_net_return": float(ordered.net_return.mean()),
                          "mean_net_alpha": float(ordered.net_alpha.mean()),
                          "total_cost": float(ordered.loc[ordered.trade, "cost_realized"].sum()),
                          "total_turnover": float(ordered.loc[ordered.trade, "turnover"].sum()),
                          "max_drawdown": float(np.min(wealth / peak - 1)),
                          "forecast_mse": float(np.mean((ordered.forecast - ordered.realized) ** 2))}
    return {"status": "descriptive_paper_only", "fixed_threshold": min_expected_alpha,
            "policies": output,
            "warning": "No threshold search or profitability claim; nonoverlap and independent validation must be verified."}
