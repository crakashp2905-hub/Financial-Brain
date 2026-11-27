"""Confirmed-swing continuation pullback detector with a 20C SMMA band filter.

This is an explicit paper-research rule, not evidence that continuation setups have edge.
The signal is known at the trigger candle close and may only be acted on next bar.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Literal

from .four_node.indicators import Cluster20
from .four_node.nodes import Node


@dataclass(frozen=True)
class ContinuationSignal:
    side: Literal["LONG", "SHORT"]
    n1_id: str
    n2_id: str
    n3_id: str
    signal_bar: int
    actionable_bar: int
    stop_reference: float
    c20: Cluster20


def evaluate_continuation_pullback(
    n1: Node,
    n2: Node,
    n3: Node,
    c20: Cluster20,
    *,
    bar: int,
    c20_at: int,
    candle_low: float,
    candle_high: float,
    close: float,
    previous_close: float,
) -> ContinuationSignal | None:
    """Return a signal for a higher-low/lower-high pullback into 20C with close turn.

    Long: confirmed low/high/higher-low, candle overlaps the 20C band and closes above
    its midpoint and prior close. Short is the symmetric high/low/lower-high condition.
    Pivots must be observable before the signal bar; 20C may include the signal close.
    """
    if bar < 0 or c20_at < 0 or c20_at > bar:
        raise ValueError("20C snapshot must be available no later than the signal bar")
    prices = (candle_low, candle_high, close, previous_close,
              c20.low, c20.mid, c20.high, n1.price, n2.price, n3.price)
    if not all(isfinite(price) for price in prices):
        raise ValueError("prices must be finite")
    if candle_low > candle_high or not candle_low <= close <= candle_high:
        raise ValueError("trigger candle prices are inconsistent")
    if not c20.low <= c20.mid <= c20.high:
        raise ValueError("20C band ordering is invalid")
    nodes = (n1, n2, n3)
    if not n1.pivot_at < n2.pivot_at < n3.pivot_at:
        raise ValueError("swing pivots must be chronologically ordered")
    if any(node.observable_at is None or node.observable_at >= bar for node in nodes):
        return None
    if n3.pivot_at >= bar:
        return None
    bullish_structure = n1.side == -1 and n2.side == 1 and n3.side == -1 and n3.price > n1.price
    bearish_structure = n1.side == 1 and n2.side == -1 and n3.side == 1 and n3.price < n1.price
    if bullish_structure and candle_low <= c20.high and close > c20.mid and close > previous_close:
        return ContinuationSignal("LONG", n1.node_id, n2.node_id, n3.node_id, bar, bar + 1,
                                  n3.price, c20)
    if bearish_structure and candle_high >= c20.low and close < c20.mid and close < previous_close:
        return ContinuationSignal("SHORT", n1.node_id, n2.node_id, n3.node_id, bar, bar + 1,
                                  n3.price, c20)
    return None
