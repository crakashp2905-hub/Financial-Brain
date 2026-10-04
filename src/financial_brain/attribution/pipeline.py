"""Where a book's return came from, layer by layer: market, signal, sizing, cost, execution, forecast.

``attribution/pnl.py`` splits a track record into market, universe selection, stock selection and
costs. This adds the layers a *decision pipeline* has and a bare track record does not, so that when a
book disappoints the answer is a stage and not a shrug:

    market      what the whole liquid universe returned, equal-weighted
    signal      the picked names, equal-weighted, minus the universe: what the ranking alone earned
    sizing      the actual weights minus equal weights on the same names: what Kelly/risk sizing added
                or took away
    cost        modelled round-trip cost of getting in and out
    execution   realised slippage minus modelled slippage, when execution was measured
    ------
    total       the book's net return, which these five sum to **exactly**, by construction

and one number that is *not* part of the sum:

    forecast    what the engine **expected** net alpha to be, minus the signal + sizing it got. Negative
                means the forecast over-promised, which is the calibration question the replay found at
                a slope of 0.71.

The identity is arithmetic, not statistical: each term is the difference of two returns that share a
term with its neighbour, so the sum telescopes.
"""
from __future__ import annotations

import numpy as np


class AttributionError(ValueError):
    pass


def decompose(*, weights, returns, universe_return: float, cost: float = 0.0,
              slippage_realised: float | None = None, slippage_modelled: float | None = None,
              expected_net_alpha: float | None = None) -> dict:
    w = np.asarray(weights, dtype=float)
    r = np.asarray(returns, dtype=float)
    if w.shape != r.shape:
        raise AttributionError("weights and returns must have the same shape")
    if (w < 0).any():
        raise AttributionError("long-only weights expected")
    held = w > 0
    if not held.any():
        return {"market": universe_return, "signal": 0.0, "sizing": 0.0, "cost": 0.0,
                "execution": 0.0, "total": 0.0, "forecast": None,
                "note": "no positions: the book earned nothing, not the market"}
    gross = float(w.sum())
    equal = float(r[held].mean())
    sized = float((w[held] * r[held]).sum() / gross)
    exec_cost = 0.0
    if slippage_realised is not None and slippage_modelled is not None:
        exec_cost = float(slippage_realised - slippage_modelled)
    parts = {"market": float(universe_return), "signal": equal - universe_return,
             "sizing": sized - equal, "cost": -float(cost), "execution": -exec_cost}
    total = float(sum(parts.values()))
    parts["total"] = total
    parts["forecast"] = (None if expected_net_alpha is None
                         else float((parts["signal"] + parts["sizing"]) - expected_net_alpha))
    parts["gross_exposure"] = gross
    return parts


def aggregate(rows: list[dict]) -> dict:
    """Mean of each layer across rebalances, with how often each layer was negative."""
    if not rows:
        raise AttributionError("nothing to aggregate")
    keys = ("market", "signal", "sizing", "cost", "execution", "total")
    out = {k: float(np.mean([r[k] for r in rows])) for k in keys}
    out["share_negative"] = {k: float(np.mean([r[k] < 0 for r in rows])) for k in keys}
    fc = [r["forecast"] for r in rows if r.get("forecast") is not None]
    out["forecast_error"] = float(np.mean(fc)) if fc else None
    out["n"] = len(rows)
    return out
