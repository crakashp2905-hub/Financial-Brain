"""Separate gross-losing trades from positive-gross trades erased by costs."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
import math


class LossEconomicsError(ValueError):
    """Invalid or unreconciled realized trade economics."""


def gross_cost_report(rows: Sequence[Mapping[str, object]], *, tolerance_inr: float = 0.01) -> dict:
    """Report counts, INR totals and notional-weighted rates for each outcome class.

    ``costs`` is a positive expense and ``net_pnl`` must equal gross minus costs.
    All input rows must be closed trades with positive entry notional.
    """
    if not math.isfinite(tolerance_inr) or tolerance_inr < 0:
        raise LossEconomicsError("tolerance_inr must be finite and nonnegative")
    if not rows:
        return {"trade_count": 0, "notional_inr": 0.0, "groups": {}}
    names = ("gross_pnl", "costs", "net_pnl", "notional")
    seen_ids: set[str] = set()
    trades = []
    for i, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise LossEconomicsError("each trade must be a mapping")
        trade_id = row.get("trade_id", f"row:{i}")
        if not isinstance(trade_id, str) or not trade_id:
            raise LossEconomicsError("trade_id must be nonempty")
        if trade_id in seen_ids:
            raise LossEconomicsError("duplicate trade_id")
        seen_ids.add(trade_id)
        values = {}
        for name in names:
            value = row.get(name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise LossEconomicsError(f"{name} must be a finite number")
            values[name] = float(value)
        if values["notional"] <= 0:
            raise LossEconomicsError("notional must be positive")
        if values["costs"] < 0:
            raise LossEconomicsError("costs must be nonnegative expenses")
        residual = values["gross_pnl"] - values["costs"] - values["net_pnl"]
        if abs(residual) > tolerance_inr:
            raise LossEconomicsError(f"gross minus costs does not reconcile to net for {trade_id}")
        values["_group"] = (
            "GROSS_NEGATIVE" if values["gross_pnl"] < 0 else
            "COST_ERASED" if values["gross_pnl"] > 0 and values["net_pnl"] <= 0 else
            "NET_POSITIVE" if values["net_pnl"] > 0 else
            "GROSS_FLAT"
        )
        trades.append(values)

    total_notional = sum(row["notional"] for row in trades)
    groups: dict[str, dict] = {}
    for label in ("GROSS_NEGATIVE", "COST_ERASED", "NET_POSITIVE", "GROSS_FLAT"):
        cohort = [row for row in trades if row["_group"] == label]
        if not cohort:
            groups[label] = {"trade_count": 0, "notional_inr": 0.0, "gross_pnl_inr": 0.0,
                             "costs_inr": 0.0, "net_pnl_inr": 0.0, "notional_share": 0.0,
                             "gross_pnl_bp": None, "costs_bp": None, "net_pnl_bp": None}
            continue
        notional = sum(row["notional"] for row in cohort)
        gross = sum(row["gross_pnl"] for row in cohort)
        costs = sum(row["costs"] for row in cohort)
        net = sum(row["net_pnl"] for row in cohort)
        groups[label] = {
            "trade_count": len(cohort), "notional_inr": notional,
            "gross_pnl_inr": gross, "costs_inr": costs, "net_pnl_inr": net,
            "notional_share": notional / total_notional,
            "gross_pnl_bp": gross / notional * 1e4,
            "costs_bp": costs / notional * 1e4,
            "net_pnl_bp": net / notional * 1e4,
        }
    return {"trade_count": len(trades), "notional_inr": total_notional, "groups": groups,
            "sign_convention": "Costs are positive expenses; net P&L equals gross P&L minus costs."}
