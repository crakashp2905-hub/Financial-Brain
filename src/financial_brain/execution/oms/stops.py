"""Deterministic stop-market and stop-limit trigger/fill semantics for paper execution.

These helpers model triggers and executable bid/ask prices. They do not claim queue
position or a fill when displayed liquidity is absent.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import math
from collections.abc import Sequence
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
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        if type(self.available_quantity) is not int or self.available_quantity < 0:
            raise ValueError("available_quantity must be a non-negative integer")
        for label, price in (("bid", self.bid), ("ask", self.ask)):
            if price is not None and (not math.isfinite(price) or price <= 0):
                raise ValueError(f"{label} must be finite and positive when supplied")
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
    triggered_at: datetime | None = None


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
    return evaluate_stop_path(
        side=side, order_kind=order_kind, stop_price=stop_price, quantity=quantity,
        observations=(observation,), submitted_at=observation.observed_at,
        limit_price=limit_price, latency_seconds=latency_seconds,
    )


def evaluate_stop_path(*, side: Side, order_kind: Literal["STOP_MARKET", "STOP_LIMIT"],
                       stop_price: float, quantity: int,
                       observations: Sequence[StopObservation], submitted_at: datetime,
                       limit_price: float | None = None,
                       latency_seconds: float = 0.0) -> StopExecution:
    """Replay an ordered quote path for a protective stop.

    A stop is triggered by the position-side executable quote (bid to exit a long,
    ask to cover a short). Latency starts at that observation. The fill must use a
    timestamped quote at or after trigger plus latency; no trigger-price fill is
    inferred. Only one snapshot's displayed quantity is consumed, since repeated
    snapshots do not prove that liquidity replenished.
    """
    if side not in ("BUY", "SELL"):
        raise ValueError("side must be BUY or SELL")
    if not math.isfinite(stop_price) or stop_price <= 0 or type(quantity) is not int or quantity <= 0:
        raise ValueError("stop_price and quantity must be positive")
    if not math.isfinite(latency_seconds) or latency_seconds < 0:
        raise ValueError("latency_seconds must be finite and non-negative")
    if submitted_at.tzinfo is None or submitted_at.utcoffset() is None:
        raise ValueError("submitted_at must be timezone-aware")
    if not observations:
        raise ValueError("at least one stop observation is required")
    ordered = tuple(observations)
    if any(right.observed_at < left.observed_at for left, right in zip(ordered, ordered[1:])):
        raise ValueError("stop observations must be time ordered")
    if order_kind == "STOP_LIMIT":
        if limit_price is None or not math.isfinite(limit_price) or limit_price <= 0:
            raise ValueError("STOP_LIMIT requires a positive limit_price")
        if (side == "SELL" and limit_price > stop_price) or (side == "BUY" and limit_price < stop_price):
            raise ValueError("limit price is on the wrong side of the stop for a protective exit")
    elif order_kind != "STOP_MARKET":
        raise ValueError("unsupported order_kind")

    trigger_index = None
    trigger_quote = None
    for index, observation in enumerate(ordered):
        if observation.observed_at < submitted_at:
            continue
        trigger_quote = observation.bid if side == "SELL" else observation.ask
        if trigger_quote is not None and (trigger_quote <= stop_price if side == "SELL"
                                          else trigger_quote >= stop_price):
            trigger_index = index
            break
    last = ordered[-1]
    if trigger_index is None:
        return StopExecution("WAITING", False, stop_price, None, 0, quantity,
                             last.observed_at, last.source, "trigger_not_observed")

    trigger_observation = ordered[trigger_index]
    trigger_time = trigger_observation.observed_at
    eligible_at = trigger_time + timedelta(seconds=latency_seconds)
    had_executable_depth = False
    for observation in ordered[trigger_index:]:
        if observation.observed_at < eligible_at:
            continue
        executable = observation.bid if side == "SELL" else observation.ask
        if executable is None or observation.available_quantity <= 0:
            continue
        had_executable_depth = True
        if order_kind == "STOP_LIMIT":
            assert limit_price is not None
            fills_at_limit = executable >= limit_price if side == "SELL" else executable <= limit_price
            if not fills_at_limit:
                continue
        fill_quantity = min(quantity, observation.available_quantity)
        status: Literal["FILLED", "PARTIAL"] = "FILLED" if fill_quantity == quantity else "PARTIAL"
        return StopExecution(status, True, stop_price, executable, fill_quantity, quantity - fill_quantity,
                             observation.observed_at, observation.source, "observed_depth_fill", trigger_time)

    if not had_executable_depth:
        reason = "latency_requires_a_later_executable_quote" if latency_seconds > 0 else "no_executable_depth"
        waiting_status: Literal["TRIGGERED_UNFILLED", "TRIGGERED_NO_LIQUIDITY"] = (
            "TRIGGERED_UNFILLED" if latency_seconds > 0 else "TRIGGERED_NO_LIQUIDITY")
        return StopExecution(waiting_status, True, stop_price, None, 0, quantity,
                             last.observed_at, last.source, reason, trigger_time)
    reason = "limit_not_marketable_queue_position_unknown"
    return StopExecution("TRIGGERED_UNFILLED", True, stop_price, None, 0, quantity,
                         last.observed_at, last.source, reason, trigger_time)

