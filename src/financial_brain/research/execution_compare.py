"""Paper-only comparison of existing minute-bar execution policies."""
from __future__ import annotations

import numpy as np

from ..execution.algos import Day, Immediate, Order, POV, Peg, Sweep, TWAP, VWAP, simulate


def compare_execution(day: Day, order: Order, *, fee_bp: float, seed: int = 7,
                      force_completion: bool = False) -> list[dict]:
    """Compare buy-side fills with the repository's simulator.

    Forced residual completion is optional; by default, unfilled orders remain unfilled.
    Bid/ask is a declared half-spread proxy,
    not reconstructed historical order-book data.
    """
    if order.qty <= 0 or fee_bp < 0 or order.start < 0 or order.end >= len(day.o):
        raise ValueError("invalid order or fee")
    arrays = (day.o, day.h, day.l, day.c, day.v, day.curve)
    if any(len(a) != len(day.o) for a in arrays) or not np.isfinite(day.o).all() or (day.v < 0).any():
        raise ValueError("invalid minute bars")
    policies = (Immediate(), TWAP(), VWAP(), POV(0.1), Peg("bid", name="passive_limit"), Sweep())
    rows = []
    for policy in policies:
        result = simulate(day, order, policy, seed=seed, force_completion=force_completion)
        rows.append({"policy": policy.name, "fill_ratio": result["fill_ratio"],
                     "unfilled_ratio": result["unfilled_ratio"],
                     "forced_completion_ratio": result["completion"] if force_completion else 0.0,
                     "pre_completion_residual_ratio": result["completion"],
                     "average_fill_price": result["avg"],
                     "implementation_shortfall_bp": result["is_bp"],
                     "all_in_cost_bp": result["is_bp"] + fee_bp if result["is_bp"] is not None else None,
                     "arrival_price": float(day.o[order.start]),
                     "assumptions": "minute OHLCV; half-spread and impact proxy; "
                                    + ("forced end-of-window completion" if force_completion else "residual remains unfilled"),
                     "unfilled_orders_modeled": not force_completion})
    return rows
