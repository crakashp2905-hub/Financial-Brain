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


def decompose_trader_layers(*, strategy_selection: float, sizing: float, market_beta: float,
                            timing: float, execution: float, taxes: float,
                            other_costs: float = 0.0) -> dict:
    """Additively decompose a realized return using supplied, evidence-backed components.

    Components are decimal returns on one common opening-capital denominator. Missing beta,
    timing or tax inputs are not replaced with zero; callers must mark those layers unavailable
    and cannot produce a complete total. This function checks arithmetic only, not attribution
    identification or causal validity.
    """
    values = {
        "strategy_selection": strategy_selection,
        "sizing": sizing,
        "market_beta": market_beta,
        "timing": timing,
        "execution": execution,
        "taxes": taxes,
        "other_costs": other_costs,
    }
    normalized: dict[str, float] = {}
    for name, raw in values.items():
        try:
            value = float(raw)
        except (TypeError, ValueError) as exc:
            raise AttributionError(f"{name} must be a finite return contribution") from exc
        if not np.isfinite(value):
            raise AttributionError(f"{name} must be a finite return contribution")
        normalized[name] = value
    total = sum(normalized.values())
    return {**normalized, "total": total, "complete": True,
            "components_sum_exactly": True,
            "unit": "decimal_return_on_common_opening_capital",
            "interpretation": "arithmetic decomposition only; supplied component identification is not validated"}


def decompose_available_trader_layers(*, strategy_selection: float | None,
                                     sizing: float | None, market_beta: float | None,
                                     timing: float | None, execution: float | None,
                                     taxes: float | None,
                                     other_costs: float | None = 0.0) -> dict:
    """Preserve missing component evidence explicitly and total only complete rows."""
    values = {"strategy_selection": strategy_selection, "sizing": sizing,
              "market_beta": market_beta, "timing": timing, "execution": execution,
              "taxes": taxes, "other_costs": other_costs}
    missing = sorted(name for name, value in values.items() if value is None)
    if missing:
        return {**values, "total": None, "complete": False, "missing_components": missing,
                "unit": "decimal_return_on_common_opening_capital"}
    assert strategy_selection is not None and sizing is not None and market_beta is not None
    assert timing is not None and execution is not None and taxes is not None
    return decompose_trader_layers(
        strategy_selection=float(strategy_selection), sizing=float(sizing),
        market_beta=float(market_beta), timing=float(timing), execution=float(execution),
        taxes=float(taxes), other_costs=0.0 if other_costs is None else float(other_costs),
    )


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
    forecast_error = (None if expected_net_alpha is None
                      else float((parts["signal"] + parts["sizing"]) - expected_net_alpha))
    result: dict[str, object] = dict(parts)
    result["total"] = total
    result["forecast"] = forecast_error
    result["gross_exposure"] = gross
    return result


def aggregate(rows: list[dict]) -> dict:
    """Mean of each layer across rebalances, with how often each layer was negative."""
    if not rows:
        raise AttributionError("nothing to aggregate")
    keys = ("market", "signal", "sizing", "cost", "execution", "total")
    out: dict[str, object] = {k: float(np.mean([r[k] for r in rows])) for k in keys}
    out["share_negative"] = {k: float(np.mean([r[k] < 0 for r in rows])) for k in keys}
    fc = [r["forecast"] for r in rows if r.get("forecast") is not None]
    out["forecast_error"] = float(np.mean(fc)) if fc else None
    out["n"] = len(rows)
    return out
