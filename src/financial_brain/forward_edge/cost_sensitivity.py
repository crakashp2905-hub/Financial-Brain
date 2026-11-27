"""Fixed slippage/impact sensitivity report over an explicit gross outcome (FE-0061)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import math
from typing import Any

from financial_brain.costs.india import CostModel, Segment
from financial_brain.forward_edge.market_events import EventKind, MarketEvent

MULTIPLIERS = (1.0, 1.25, 1.5, 2.0)


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
    observed_quote_sha256: str | None = None
    observed_cost_floor_inr: float | None = None
    observed_spread_and_depth_inr: float | None = None


def _levels(quote: MarketEvent, side: str) -> tuple[tuple[Decimal, int], ...]:
    payload = quote.payload
    key = "ask_levels" if side == "BUY" else "bid_levels"
    raw = payload.get(key)
    if raw is None:
        depth = payload.get("depth") or {}
        raw = depth.get("sell" if side == "BUY" else "buy") if isinstance(depth, dict) else None
    if not isinstance(raw, (list, tuple)) or not raw:
        raise SensitivityError(f"observed {side.lower()} depth is required for the quote cost floor")
    result: list[tuple[Decimal, int]] = []
    for row in raw:
        try:
            if isinstance(row, dict):
                price, quantity = Decimal(str(row["price"])), row["quantity"]
            elif isinstance(row, (list, tuple)) and len(row) == 2:
                price, quantity = Decimal(str(row[0])), row[1]
            else:
                raise ValueError("invalid level")
            if (not price.is_finite() or price <= 0 or isinstance(quantity, bool)
                    or type(quantity) is not int or quantity <= 0):
                raise ValueError("invalid price or visible quantity")
        except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
            raise SensitivityError("quote depth contains an invalid price/quantity level") from exc
        result.append((price, quantity))
    if any(result[index][0] > result[index + 1][0] if side == "BUY"
           else result[index][0] < result[index + 1][0] for index in range(len(result) - 1)):
        raise SensitivityError("quote depth must be ordered best-to-worst")
    return tuple(result)


def _walk(levels: tuple[tuple[Decimal, int], ...], quantity: int) -> Decimal:
    remaining, notional = quantity, Decimal(0)
    for price, visible in levels:
        take = min(remaining, visible)
        notional += price * take
        remaining -= take
        if remaining == 0:
            break
    if remaining:
        raise SensitivityError("observed two-sided depth cannot execute the requested round-trip quantity")
    return notional / quantity


def cost_sensitivity(*, gross_pnl: float, order_value: float, base_slippage: float | None,
                     base_impact: float | None, calibration_source: str | None,
                     segment: Segment, bucket: str, model: CostModel | None = None,
                     observed_quote: MarketEvent | None = None, quantity: int | None = None,
                     as_of: datetime | None = None, max_quote_age_seconds: float = 5.0
                     ) -> CostSensitivity:
    values = (gross_pnl, order_value)
    if any(not isinstance(x, (int, float)) or x != x or abs(x) == float("inf") for x in values):
        raise SensitivityError("gross P&L and order value must be finite")
    if order_value <= 0 or base_slippage is None:
        raise SensitivityError("positive order value and explicit base slippage are required")
    if not math.isfinite(base_slippage) or base_slippage < 0:
        raise SensitivityError("base slippage must be finite and non-negative")
    if base_impact is not None and (not math.isfinite(base_impact) or base_impact < 0):
        raise SensitivityError("additional impact must be finite and non-negative")
    if calibration_source is None or not calibration_source.strip():
        raise SensitivityError("calibration_source is required; identify observed or provisional estimates")
    cost_model = model or CostModel()
    fees = cost_model.round_trip(turnover=order_value, segment=segment, bucket=bucket,
                                 include_impact=False)["total"]
    quote_hash = None
    observed_floor = observed_execution_cost = None
    if observed_quote is not None:
        if observed_quote.kind is not EventKind.QUOTE:
            raise SensitivityError("cost floor requires an observed two-sided quote event")
        if (type(quantity) is not int or quantity < 1 or as_of is None or as_of.tzinfo is None
                or as_of.utcoffset() is None or not math.isfinite(max_quote_age_seconds)
                or max_quote_age_seconds < 0):
            raise SensitivityError("observed cost floor requires quantity and a valid as_of/quote-age contract")
        now = as_of.astimezone(timezone.utc)
        available = observed_quote.available_at.astimezone(timezone.utc)
        age = (now - available).total_seconds()
        if age < 0 or age > max_quote_age_seconds:
            raise SensitivityError("observed quote is future-dated or stale for the cost floor")
        bid, ask = Decimal(str(observed_quote.payload["bid"])), Decimal(str(observed_quote.payload["ask"]))
        if bid <= 0 or ask < bid:
            raise SensitivityError("cost floor requires a valid uncrossed two-sided quote")
        mid = (bid + ask) / 2
        if not math.isclose(order_value, float(mid * quantity), rel_tol=1e-6, abs_tol=0.01):
            raise SensitivityError("order_value must match the observed quote midpoint times quantity")
        bids, asks = _levels(observed_quote, "SELL"), _levels(observed_quote, "BUY")
        if bids[0][0] != bid or asks[0][0] != ask:
            raise SensitivityError("depth must begin with the quote's observed best bid and ask")
        buy_average, sell_average = _walk(asks, quantity), _walk(bids, quantity)
        spread_cost = (ask - bid) * quantity
        depth_impact = ((buy_average - ask) + (bid - sell_average)) * quantity
        observed_execution_cost = float(spread_cost + depth_impact)
        observed_floor = float(Decimal(str(fees)) + spread_cost + depth_impact)
        quote_hash = observed_quote.sha256()
        additional_impact = base_impact or 0.0
    else:
        additional_impact = base_impact or 0.0

    base_cost = fees + (observed_execution_cost or 0.0) + (additional_impact or 0.0)
    scenarios = []
    for cost_multiplier in MULTIPLIERS:
        for slippage_multiplier in MULTIPLIERS:
            stressed_cost = base_cost * cost_multiplier
            slippage = base_slippage * slippage_multiplier
            total = stressed_cost + slippage
            net = gross_pnl - total
            scenarios.append({"cost_multiplier": cost_multiplier,
                              "slippage_multiplier": slippage_multiplier,
                              "fees": fees * cost_multiplier,
                              "observed_spread_and_depth": (observed_execution_cost or 0.0) * cost_multiplier,
                              "additional_impact": (additional_impact or 0.0) * cost_multiplier,
                              "slippage": slippage, "total_cost": total, "net_pnl": net,
                              "net_positive": net > 0})
    source_claims_observed = calibration_source.lower().startswith(("observed:", "broker:", "kite:"))
    calibrated = quote_hash is not None and source_claims_observed
    return CostSensitivity(float(gross_pnl), float(fees), float(base_slippage),
                           float(additional_impact or 0.0), calibration_source, calibrated,
                           tuple(scenarios), quote_hash, observed_floor, observed_execution_cost)
