"""Auditable summary of a paper engine run's fills and assumptions."""
from __future__ import annotations

from collections import Counter


def audit(run) -> dict:
    """Return coverage and accounting facts without relabelling simulated fills.

    ``run`` is the paper.engine.Run object. Missing or partial fills remain visible;
    this report is diagnostic and never promotes a strategy.
    """
    trades = list(run.trades)
    kinds = Counter(t.fill_kind for t in trades)
    intended = sum(max(int(t.intended_shares), 0) for t in trades)
    filled = sum(max(int(t.shares), 0) for t in trades)
    costs = sum(float(t.cost_inr) for t in trades)
    return {
        "experiment_id": run.experiment_id,
        "trades": len(trades),
        "fill_kinds": dict(kinds),
        "intended_shares": intended,
        "filled_shares": filled,
        "unfilled_shares": max(intended - filled, 0),
        "fill_ratio": filled / intended if intended else 0.0,
        "costs_inr": costs,
        "equity_rows": len(run.equity),
        "warnings": list(run.warnings),
        "paper_only": True,
    }
