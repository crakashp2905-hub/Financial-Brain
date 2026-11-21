"""Conservative passive limit fill model with explicit unknown queue position (FE-0058)."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


class PassiveQueueError(ValueError):
    """Malformed passive fill evidence."""


@dataclass(frozen=True)
class PassiveFill:
    status: str
    requested_quantity: int
    filled_quantity: int
    queue_position_known: bool
    queue_ahead: int | None
    evidence_quantity: int
    reason: str


def simulate_passive_fill(*, side: str, limit_price: Decimal, quantity: int,
                          best_bid: Decimal | None, best_ask: Decimal | None,
                          traded_at_limit: int, queue_ahead: int | None = None,
                          queue_position_known: bool = False,
                          candle_touched_limit: bool = False) -> PassiveFill:
    """Only trade-through volume beyond disclosed queue can fill; a candle touch is insufficient."""
    if side not in {"BUY", "SELL"} or limit_price <= 0 or quantity < 1 or traded_at_limit < 0:
        raise PassiveQueueError("invalid side, price, quantity or trade volume")
    if queue_position_known and (queue_ahead is None or queue_ahead < 0):
        raise PassiveQueueError("known queue position requires non-negative queue_ahead")
    if not queue_position_known and queue_ahead is not None:
        raise PassiveQueueError("queue_ahead cannot be asserted when queue position is unknown")
    if best_bid is None or best_ask is None or best_bid <= 0 or best_ask < best_bid:
        return PassiveFill("MISSED", quantity, 0, queue_position_known, queue_ahead, 0,
                           "INVALID_OR_MISSING_BOOK")
    resting = limit_price >= best_bid if side == "BUY" else limit_price <= best_ask
    if not resting:
        return PassiveFill("MISSED", quantity, 0, queue_position_known, queue_ahead, 0,
                           "LIMIT_NOT_RESTING_IN_BOOK")
    if candle_touched_limit and traded_at_limit == 0:
        return PassiveFill("MISSED", quantity, 0, queue_position_known, queue_ahead, 0,
                           "CANDLE_TOUCH_WITHOUT_TRADE_EVIDENCE")
    if traded_at_limit == 0:
        return PassiveFill("MISSED", quantity, 0, queue_position_known, queue_ahead, 0,
                           "NO_TRADE_THROUGH_EVIDENCE")
    if not queue_position_known:
        return PassiveFill("MISSED", quantity, 0, False, None, traded_at_limit,
                           "UNKNOWN_QUEUE_POSITION_CONSERVATIVE_NO_FILL")
    executable = max(0, traded_at_limit - int(queue_ahead or 0))
    filled = min(quantity, executable)
    if filled == 0:
        return PassiveFill("MISSED", quantity, 0, True, queue_ahead, traded_at_limit,
                           "TRADED_VOLUME_CONSUMED_BY_QUEUE_AHEAD")
    return PassiveFill("FILLED" if filled == quantity else "PARTIAL", quantity, filled, True,
                       queue_ahead, traded_at_limit, "QUEUE_ADJUSTED_TRADE_THROUGH")
