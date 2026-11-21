"""Deterministic scenario fill planner: adversity, limits, depth, slices and freeze (FE-0059)."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal


class ExecutionScenarioError(ValueError):
    """Malformed execution scenario."""


@dataclass(frozen=True)
class ScenarioFill:
    status: str
    requested_quantity: int
    filled_quantity: int
    inventory_delta: int
    average_price: Decimal | None
    child_quantities: tuple[int, ...]
    reject_reason: str | None
    adverse_slippage_bps: Decimal


def evaluate_scenario(*, side: str, quantity: int, touch_price: Decimal, limit_price: Decimal,
                      tick_size: Decimal, visible_quantity: int, freeze_limit: int,
                       slice_size: int, adverse_slippage_bps: Decimal,
                       price_band: tuple[Decimal, Decimal] | None = None) -> ScenarioFill:
    if side not in {"BUY", "SELL"} or quantity < 1 or min(touch_price, limit_price, tick_size) <= 0:
        raise ExecutionScenarioError("invalid side, quantity or prices")
    if visible_quantity < 0 or freeze_limit < 1 or slice_size < 1 or adverse_slippage_bps < 0:
        raise ExecutionScenarioError("invalid visible quantity, freeze, slice or slippage input")
    if quantity > freeze_limit:
        return ScenarioFill("REJECTED", quantity, 0, 0, None, (), "FREEZE_LIMIT_EXCEEDED",
                            adverse_slippage_bps)
    filled_total = min(quantity, visible_quantity)
    if filled_total == 0:
        return ScenarioFill("MISSED", quantity, 0, 0, None, (), "NO_VISIBLE_LIQUIDITY", adverse_slippage_bps)
    children: list[int] = []
    remaining = filled_total
    while remaining:
        child = min(remaining, slice_size, freeze_limit)
        children.append(child)
        remaining -= child
    multiplier = Decimal(1) + adverse_slippage_bps / Decimal(10_000) if side == "BUY" else \
        Decimal(1) - adverse_slippage_bps / Decimal(10_000)
    stressed = touch_price * multiplier
    rounding = ROUND_CEILING if side == "BUY" else ROUND_FLOOR
    stressed = (stressed / tick_size).to_integral_value(rounding=rounding) * tick_size
    if price_band is not None:
        lower, upper = price_band
        if not lower.is_finite() or not upper.is_finite() or lower <= 0 or lower > upper:
            raise ExecutionScenarioError("price band must be finite, positive and ordered")
        if not lower <= stressed <= upper:
            return ScenarioFill("REJECTED", quantity, 0, 0, None, tuple(children), "PRICE_BAND",
                                adverse_slippage_bps)
    crosses_limit = stressed <= limit_price if side == "BUY" else stressed >= limit_price
    if not crosses_limit:
        return ScenarioFill("MISSED", quantity, 0, 0, None, tuple(children), "ADVERSE_PRICE_BREACHES_LIMIT",
                            adverse_slippage_bps)
    status = "FILLED" if filled_total == quantity else "PARTIAL"
    delta = filled_total if side == "BUY" else -filled_total
    return ScenarioFill(status, quantity, filled_total, delta, stressed, tuple(children), None,
                        adverse_slippage_bps)


def evaluate_passive_scenario(*, side: str, quantity: int, limit_price: Decimal, tick_size: Decimal,
                              freeze_limit: int, slice_size: int, adverse_slippage_bps: Decimal,
                              trade_prints: tuple[tuple[Decimal, int], ...],
                              price_band: tuple[Decimal, Decimal] | None = None) -> ScenarioFill:
    """Simulate a passive limit from observed post-eligibility trade prints.

    Touching the limit is not enough to fill. A print must trade strictly through it;
    fills are recorded at the limit. Adverse selection is carried separately as a monetary
    cost by the receipt layer, preserving the submitted limit price. Queue position remains
    unknown, so this is an optimistic upper bound on fills.
    """
    if side not in {"BUY", "SELL"} or quantity < 1 or min(limit_price, tick_size) <= 0:
        raise ExecutionScenarioError("invalid side, quantity or prices")
    if freeze_limit < 1 or slice_size < 1 or adverse_slippage_bps < 0:
        raise ExecutionScenarioError("invalid freeze, slice or slippage input")
    if quantity > freeze_limit:
        return ScenarioFill("REJECTED", quantity, 0, 0, None, (), "FREEZE_LIMIT_EXCEEDED",
                            adverse_slippage_bps)
    filled = 0
    for price, printed_quantity in trade_prints:
        if not price.is_finite() or price <= 0 or printed_quantity < 0:
            raise ExecutionScenarioError("trade prints need finite positive prices and non-negative quantity")
        through = price < limit_price if side == "BUY" else price > limit_price
        if through and printed_quantity:
            filled += min(quantity - filled, printed_quantity)
            if filled == quantity:
                break
    if filled == 0:
        return ScenarioFill("MISSED", quantity, 0, 0, None, (), "NO_TRADE_THROUGH_LIMIT",
                            adverse_slippage_bps)
    average = limit_price
    children: list[int] = []
    remaining = filled
    while remaining:
        child = min(remaining, slice_size, freeze_limit)
        children.append(child)
        remaining -= child
    if price_band is not None:
        lower, upper = price_band
        if not lower.is_finite() or not upper.is_finite() or lower <= 0 or lower > upper:
            raise ExecutionScenarioError("price band must be finite, positive and ordered")
        if not lower <= average <= upper:
            return ScenarioFill("REJECTED", quantity, 0, 0, None, tuple(children), "PRICE_BAND",
                                adverse_slippage_bps)
    status = "FILLED" if filled == quantity else "PARTIAL"
    delta = filled if side == "BUY" else -filled
    return ScenarioFill(status, quantity, filled, delta, average, tuple(children), None,
                        adverse_slippage_bps)
