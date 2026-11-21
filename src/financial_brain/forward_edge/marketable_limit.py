"""Quote-based marketable-limit planning with visible size and tick constraints (FE-0057)."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from financial_brain.forward_edge.market_events import EventKind, MarketEvent


class MarketableLimitError(ValueError):
    """Marketable-limit request cannot be safely planned."""


@dataclass(frozen=True)
class MarketablePlan:
    decision: str
    side: str
    requested_quantity: int
    executable_quantity: int
    limit_price: Decimal | None
    visible_quantity: int
    quote_event_sha256: str
    reason: str


def plan_marketable_limit(*, quote: MarketEvent, side: str, quantity: int,
                          tick_size: Decimal, max_slippage_bps: Decimal) -> MarketablePlan:
    if quote.kind != EventKind.QUOTE:
        raise MarketableLimitError("marketable limit requires a quote event")
    if side not in {"BUY", "SELL"} or quantity < 1 or tick_size <= 0 or max_slippage_bps < 0:
        raise MarketableLimitError("invalid side, quantity, tick size or slippage cap")
    p = quote.payload
    key = "ask_quantity" if side == "BUY" else "bid_quantity"
    visible_value = p.get(key)
    if isinstance(visible_value, bool) or not isinstance(visible_value, int) or visible_value < 0:
        return MarketablePlan("NO_FILL", side, quantity, 0, None, 0, quote.sha256(), "VISIBLE_SIZE_MISSING")
    visible = visible_value
    executable = min(quantity, visible)
    if executable <= 0:
        return MarketablePlan("NO_FILL", side, quantity, 0, None, visible, quote.sha256(), "NO_VISIBLE_LIQUIDITY")
    touch = Decimal(str(p["ask"] if side == "BUY" else p["bid"]))
    cap = Decimal(str(p["bid"] if side == "BUY" else p["ask"]))
    raw_limit = touch * (Decimal(1) + max_slippage_bps / Decimal(10_000) if side == "BUY"
                         else Decimal(1) - max_slippage_bps / Decimal(10_000))
    rounding = ROUND_CEILING if side == "BUY" else ROUND_FLOOR
    limit_price = (raw_limit / tick_size).to_integral_value(rounding=rounding) * tick_size
    # At zero slippage allowance the limit must still cross the touch by using the touch price itself.
    if side == "BUY":
        limit_price = max(limit_price, touch)
        if limit_price > cap and max_slippage_bps == 0:
            limit_price = touch
    else:
        limit_price = min(limit_price, touch)
        if limit_price < cap and max_slippage_bps == 0:
            limit_price = touch
    return MarketablePlan("PARTIAL" if executable < quantity else "READY", side, quantity, executable,
                          limit_price, visible, quote.sha256(),
                          "VISIBLE_SIZE_CAP" if executable < quantity else "MARKETABLE_LIMIT_AT_TOUCH")
