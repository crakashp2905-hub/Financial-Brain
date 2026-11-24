"""Fail-closed projection of requested sleeve weights into portfolio limits."""
from __future__ import annotations

import math

import numpy as np


class WeightProjectionError(ValueError):
    """Invalid requested weights or infeasible portfolio limits."""


def project_feasible_weights(
    requested: np.ndarray | list[float],
    *,
    lower: np.ndarray | list[float],
    upper: np.ndarray | list[float],
    gross_budget: float,
    allow_short: bool,
    max_net_exposure: float | None = None,
) -> dict[str, object]:
    """Clip by sleeve limits and uniformly scale to gross/net budgets.

    Bounds are sleeve-specific and must permit a zero allocation so that cash
    remains feasible. When shorting is disabled, negative lower bounds are
    rejected and all signed weights are projected long-only. This is a
    feasibility primitive, not an optimizer or a trading instruction.
    """
    w = _vector(requested, "requested")
    lo, hi = _vector(lower, "lower"), _vector(upper, "upper")
    if lo.shape != w.shape or hi.shape != w.shape:
        raise WeightProjectionError("requested weights and lower/upper limits must have equal shape")
    if np.any(lo > hi):
        raise WeightProjectionError("a sleeve lower limit exceeds its upper limit")
    if np.any(lo > 0) or np.any(hi < 0):
        raise WeightProjectionError("every sleeve's bounds must include zero for cash feasibility")
    if not allow_short and np.any(lo < 0):
        raise WeightProjectionError("short-enabled lower bounds require allow_short=True")
    if not math.isfinite(gross_budget) or gross_budget < 0:
        raise WeightProjectionError("gross_budget must be finite and non-negative")
    if max_net_exposure is not None and (
        not math.isfinite(max_net_exposure) or max_net_exposure < 0
    ):
        raise WeightProjectionError("max_net_exposure must be finite and non-negative")

    clipped = np.clip(w, lo, hi)
    if not allow_short:
        clipped = np.maximum(clipped, 0.0)
    gross_scale = min(1.0, gross_budget / float(np.abs(clipped).sum())) if np.abs(clipped).sum() else 1.0
    result = clipped * gross_scale
    net_scale = 1.0
    net = float(result.sum())
    if max_net_exposure is not None and abs(net) > max_net_exposure and abs(net) > 0:
        net_scale = max_net_exposure / abs(net)
        result *= net_scale
    gross = float(np.abs(result).sum())
    net = float(result.sum())
    # Tolerances guard floating point roundoff; material violations are bugs.
    if gross > gross_budget + 1e-10:
        raise WeightProjectionError("projection exceeded gross exposure budget")
    if max_net_exposure is not None and abs(net) > max_net_exposure + 1e-10:
        raise WeightProjectionError("projection exceeded net exposure budget")
    if np.any(result < lo - 1e-10) or np.any(result > hi + 1e-10):
        raise WeightProjectionError("projection exceeded a sleeve-specific limit")
    return {
        "weights": result,
        "gross_exposure": gross,
        "net_exposure": net,
        "long_exposure": float(np.maximum(result, 0.0).sum()),
        "short_exposure": float(np.minimum(result, 0.0).sum()),
        "gross_scale": gross_scale,
        "net_scale": net_scale,
        "allow_short": allow_short,
        "feasible": True,
    }


def _vector(value: np.ndarray | list[float], name: str) -> np.ndarray:
    try:
        result = np.asarray(value, dtype=float)
    except (TypeError, ValueError, OverflowError) as exc:
        raise WeightProjectionError(f"{name} must be a finite one-dimensional vector") from exc
    if result.ndim != 1 or result.size == 0 or not np.isfinite(result).all():
        raise WeightProjectionError(f"{name} must be a finite non-empty one-dimensional vector")
    return result


__all__ = ["WeightProjectionError", "project_feasible_weights"]
