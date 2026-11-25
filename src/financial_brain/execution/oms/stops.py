"""Deterministic stop-market and stop-limit trigger/fill semantics for paper execution.

These helpers model triggers and executable bid/ask prices. They do not claim queue
position or a fill when displayed liquidity is absent.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

Side = Literal["BUY", "SELL"]


@dataclass(frozen=True)
class StopObservation:
    observed_at: datetime
    bid: float | None
    ask: float | None
    available_quantity: int
    source: str

    def __post_init__(self) -> None:
        if self.available_quantity < 0:
            raise ValueError("available_quantity cannot be negative")
        for label, price in (("bid", self.bid), ("ask", self.ask)):
            if price is not None and price <= 0:
                raise ValueError(f"{label} must be positive when supplied")
        if self.bid is not None and self.ask is not None and self.bid > self.ask:
            raise ValueError("bid cannot exceed ask")
        if not self.source.strip():
            raise ValueError("source is required")


@dataclass(frozen=True)
class StopExecution:
    status: Literal["WAITING", "TRIGGERED_NO_LIQUIDITY", "PARTIAL", "FILLED", "TRIGGERED_UNFILLED"]
    triggered: bool
    trigger_price: float
    fill_price: float | None
    fill_quantity: int
    remaining_quantity: int
    observed_at: datetime
    source: str
    reason: str


def evaluate_stop(*, side: Side, order_kind: Literal["STOP_MARKET", "STOP_LIMIT"], stop_price: float,
                  quantity: int, observation: StopObservation, limit_price: float | None = None,
                  latency_seconds: float = 0.0) -> StopExecution:
    """Evaluate a protective exit against one observed quote/depth snapshot.

    For a long position the exit is SELL and triggers on bid <= stop. For a short
    position the exit is BUY and triggers on ask >= stop. Stop-market slippage uses
    the observed executable side of book; stop-limit may trigger without filling.
    Latency is reported as unavailable when positive because no later quote is
    provided to support a post-latency fill.
    """
    if side not in ("BUY", "SELL"):
        raise ValueError("side must be BUY or SELL")
    if stop_price <= 0 or quantity <= 0:
        raise ValueError("stop_price and quantity must be positive")
    if latency_seconds < 0:
        raise ValueError("latency_seconds cannot be negative")
    if order_kind == "STOP_LIMIT":
        if limit_price is None or limit_price <= 0:
            raise ValueError("STOP_LIMIT requires a positive limit_price")
        if (side == "SELL" and limit_price > stop_price) or (side == "BUY" and limit_price < stop_price):
            raise ValueError("limit price is on the wrong side of the stop for a protective exit")
    elif order_kind != "STOP_MARKET":
        raise ValueError("unsupported order_kind")

    trigger_observation = observation.bid if side == "SELL" else observation.ask
    triggered = trigger_observation is not None and (
        trigger_observation <= stop_price if side == "SELL" else trigger_observation >= stop_price
    )
    executable = trigger_observation
    if not triggered:
        return StopExecution("WAITING", False, stop_price, None, 0, quantity,
                             observation.observed_at, observation.source, "trigger_not_observed")
    if latency_seconds > 0:
        return StopExecution("TRIGGERED_UNFILLED", True, stop_price, None, 0, quantity,
                             observation.observed_at, observation.source,
                             "latency_requires_a_later_executable_quote")
    if observation.available_quantity <= 0 or executable is None:
        return StopExecution("TRIGGERED_NO_LIQUIDITY", True, stop_price, None, 0, quantity,
                             observation.observed_at, observation.source, "no_executable_depth")

    if order_kind == "STOP_LIMIT":
        assert limit_price is not None
        fills_at_limit = executable >= limit_price if side == "SELL" else executable <= limit_price
        if not fills_at_limit:
            return StopExecution("TRIGGERED_UNFILLED", True, stop_price, None, 0, quantity,
                                 observation.observed_at, observation.source, "triggered_but_limit_not_marketable")
    fill_quantity = min(quantity, observation.available_quantity)
    status: Literal["FILLED", "PARTIAL"] = "FILLED" if fill_quantity == quantity else "PARTIAL"
    return StopExecution(status, True, stop_price, executable, fill_quantity, quantity - fill_quantity,
                         observation.observed_at, observation.source, "observed_depth_fill")

