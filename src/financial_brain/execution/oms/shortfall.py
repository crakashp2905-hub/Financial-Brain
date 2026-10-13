"""Implementation shortfall of real fills, against what the simulator predicted for the same order.

For each parent order (all its children together):

    realised_bp   (average fill - arrival price) / arrival, signed so that positive hurts (a buy paid up, a sell sold
                  down), over the filled quantity
    predicted_bp  the same for the simulator (``execution/simulator.execute``) walking the order through that
                  session's minute bars at the same participation cap
    ratio         realised / predicted - the number the rollout criteria read (median over orders)
    unfilled      the share of the parent that did not fill (opportunity cost is reported apart, not mixed in)

A ratio above 1 means the cost model the backtests used is optimistic for this account and these names, and every
backtest net figure is overstated by roughly that factor on its cost line.
"""
from __future__ import annotations

import numpy as np

from . import model as M


def realised_bp(orders: list[M.Order]) -> dict:
    filled = [o for o in orders if o.filled_qty and o.avg_price and o.arrival_price]
    if not filled:
        return {"filled_qty": 0, "realised_bp": None}
    q = sum(o.filled_qty for o in filled)
    side = filled[0].side
    avg = sum(o.avg_price * o.filled_qty for o in filled) / q
    arrival = filled[0].arrival_price
    sign = 1 if side == M.BUY else -1
    total = sum(o.qty for o in orders)
    return {"filled_qty": q, "avg_price": avg, "arrival": arrival,
            "realised_bp": sign * (avg / arrival - 1) * 1e4, "unfilled_share": 1 - q / total if total else 0.0}


def predicted_bp(con, *, symbol: str, session, side: str, qty: int, arrival: float, start_minute: int = 0,
                 participation: float = 0.10) -> float | None:
    """The simulator's shortfall for the same order on the same session's minute bars, from the same start minute
    and participation (None without bars, or when the simulator could not fill any of it)."""
    from ...execution import simulator as sim
    try:
        f = sim.execute(con, symbol=symbol, session=session, side=sim.BUY if side == M.BUY else sim.SELL,
                        target_shares=qty, decision_price=arrival, start_minute=start_minute,
                        participation=participation)
    except sim.ExecutionError:
        return None
    return f.shortfall_bps


def summary(rows: list[dict]) -> dict:
    """``rows``: one per parent with realised_bp and predicted_bp."""
    r = [x for x in rows if x.get("realised_bp") is not None and x.get("predicted_bp")]
    ratios = [x["realised_bp"] / x["predicted_bp"] for x in r if x["predicted_bp"] > 0]
    return {"parents": len(rows), "compared": len(r),
            "median_realised_bp": float(np.median([x["realised_bp"] for x in r])) if r else None,
            "median_predicted_bp": float(np.median([x["predicted_bp"] for x in r])) if r else None,
            "median_ratio": float(np.median(ratios)) if ratios else None}
