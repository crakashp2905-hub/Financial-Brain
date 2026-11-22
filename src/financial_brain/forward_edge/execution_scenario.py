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
    # Price and quantity at each consumed displayed-book level, after adverse stress.
    # Kept additive so legacy one-touch and passive scenarios remain readable.
    level_fills: tuple[tuple[Decimal, int], ...] = ()


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


def evaluate_depth_scenario(*, side: str, quantity: int,
                            levels: tuple[tuple[Decimal, int], ...], limit_price: Decimal,
                            tick_size: Decimal, freeze_limit: int, slice_size: int,
                            adverse_slippage_bps: Decimal,
                            price_band: tuple[Decimal, Decimal] | None = None) -> ScenarioFill:
    """Walk the observed opposing book and fill only displayed, limit-feasible depth.

    ``levels`` must be best-to-worst asks for BUY and bids for SELL. Adverse slippage is
    applied to each level and rounded against the trader before checking the limit/band.
    The function makes no queue-position or replenishment assumption: depth not present in
    this quote cannot fill. A remainder is PARTIAL; zero executable depth is MISSED.
    """
    if (side not in {"BUY", "SELL"} or quantity < 1
            or not isinstance(limit_price, Decimal) or not isinstance(tick_size, Decimal)
            or not limit_price.is_finite() or not tick_size.is_finite()
            or limit_price <= 0 or tick_size <= 0):
        raise ExecutionScenarioError("invalid side, quantity or prices")
    if (freeze_limit < 1 or slice_size < 1 or not isinstance(adverse_slippage_bps, Decimal)
            or not adverse_slippage_bps.is_finite()
            or adverse_slippage_bps < 0 or adverse_slippage_bps >= Decimal(10_000)):
        raise ExecutionScenarioError("invalid freeze, slice or slippage input")
    if quantity > freeze_limit:
        return ScenarioFill("REJECTED", quantity, 0, 0, None, (), "FREEZE_LIMIT_EXCEEDED",
                            adverse_slippage_bps)
    if not levels:
        return ScenarioFill("MISSED", quantity, 0, 0, None, (), "NO_VISIBLE_LIQUIDITY",
                            adverse_slippage_bps)
    previous: Decimal | None = None
    for price, displayed in levels:
        if not isinstance(price, Decimal) or not price.is_finite() or price <= 0 \
                or type(displayed) is not int or displayed <= 0:
            raise ExecutionScenarioError("book levels require finite positive prices and integral quantities")
        if previous is not None and (price < previous if side == "BUY" else price > previous):
            raise ExecutionScenarioError("opposing book levels are not ordered best-to-worst")
        previous = price

    multiplier = (Decimal(1) + adverse_slippage_bps / Decimal(10_000) if side == "BUY"
                  else Decimal(1) - adverse_slippage_bps / Decimal(10_000))
    rounding = ROUND_CEILING if side == "BUY" else ROUND_FLOOR
    remaining = quantity
    fills: list[tuple[Decimal, int]] = []
    guard_reason: str | None = None
    for raw_price, displayed in levels:
        stressed = (raw_price * multiplier / tick_size).to_integral_value(rounding=rounding) * tick_size
        if price_band is not None:
            lower, upper = price_band
            if (not isinstance(lower, Decimal) or not isinstance(upper, Decimal)
                    or not lower.is_finite() or not upper.is_finite()
                    or lower <= 0 or lower > upper):
                raise ExecutionScenarioError("price band must be finite, positive and ordered")
            if not lower <= stressed <= upper:
                guard_reason = "PRICE_BAND"
                break
        feasible = stressed <= limit_price if side == "BUY" else stressed >= limit_price
        if not feasible:
            guard_reason = "ADVERSE_PRICE_BREACHES_LIMIT"
            break
        take = min(remaining, displayed)
        if take:
            fills.append((stressed, take))
            remaining -= take
        if remaining == 0:
            break

    filled = quantity - remaining
    if filled == 0:
        status = "REJECTED" if guard_reason == "PRICE_BAND" else "MISSED"
        return ScenarioFill(status, quantity, 0, 0, None, (), guard_reason or "NO_VISIBLE_LIQUIDITY",
                            adverse_slippage_bps)
    weighted = sum((price * amount for price, amount in fills), Decimal(0)) / Decimal(filled)
    children: list[int] = []
    child_remaining = filled
    while child_remaining:
        child = min(child_remaining, slice_size, freeze_limit)
        children.append(child)
        child_remaining -= child
    status = "FILLED" if filled == quantity else "PARTIAL"
    delta = filled if side == "BUY" else -filled
    return ScenarioFill(status, quantity, filled, delta, weighted, tuple(children), guard_reason,
                        adverse_slippage_bps, tuple(fills))


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
