"""Fixed slippage/impact sensitivity report over an explicit gross outcome (FE-0061)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from financial_brain.costs.india import CostModel, Segment

MULTIPLIERS = (1.0, 1.5, 2.0)


class SensitivityError(ValueError):
    """Invalid or incomplete execution-cost sensitivity input."""


@dataclass(frozen=True)
class CostSensitivity:
    gross_pnl: float
    statutory_and_broker_fees: float
    base_slippage: float
    base_impact: float
    calibration_source: str
    calibrated: bool
    scenarios: tuple[dict[str, Any], ...]


def cost_sensitivity(*, gross_pnl: float, order_value: float, base_slippage: float | None,
                     base_impact: float | None, calibration_source: str | None,
                     segment: Segment, bucket: str, model: CostModel | None = None) -> CostSensitivity:
    values = (gross_pnl, order_value)
    if any(not isinstance(x, (int, float)) or x != x or abs(x) == float("inf") for x in values):
        raise SensitivityError("gross P&L and order value must be finite")
    if order_value <= 0 or base_slippage is None or base_impact is None:
        raise SensitivityError("positive order value and explicit base slippage/impact are required")
    if min(base_slippage, base_impact) < 0:
        raise SensitivityError("base slippage and impact must be non-negative")
    if calibration_source is None or not calibration_source.strip():
        raise SensitivityError("calibration_source is required; identify observed or provisional estimates")
    cost_model = model or CostModel()
    fees = cost_model.round_trip(turnover=order_value, segment=segment, bucket=bucket,
                                 include_impact=False)["total"]
    scenarios = []
    for multiplier in MULTIPLIERS:
        slippage = base_slippage * multiplier
        impact = base_impact * multiplier
        total = fees + slippage + impact
        net = gross_pnl - total
        scenarios.append({"multiplier": multiplier, "fees": fees, "slippage": slippage,
                          "impact": impact, "total_cost": total, "net_pnl": net,
                          "net_positive": net > 0})
    calibrated = calibration_source.lower().startswith(("observed:", "broker:", "kite:"))
    return CostSensitivity(float(gross_pnl), float(fees), float(base_slippage), float(base_impact),
                           calibration_source, calibrated, tuple(scenarios))
