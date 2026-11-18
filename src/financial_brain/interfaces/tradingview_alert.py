"""Validate TradingView alerts as observations, never as orders.

Transport authentication belongs to a separately deployed ingress. This module
only normalizes an already authenticated JSON payload for research/paper use.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


class AlertRejected(ValueError):
    """The payload cannot be used as a current, identifiable observation."""


@dataclass(frozen=True)
class TradingViewAlert:
    alert_id: str
    strategy: str
    exchange: str
    symbol: str
    direction: str
    event_time: datetime
    received_time: datetime
    reference_price: float


def parse_alert(
    raw: bytes,
    *,
    received_time: datetime,
    allowed_strategies: frozenset[str],
    max_age: timedelta = timedelta(minutes=2),
) -> TradingViewAlert:
    """Parse a bounded, allowlisted alert; do not infer a fill or trade intent.

    ``alert_id`` must be stable across webhook retries so the caller can deduplicate
    it. A production ingress must authenticate, persist, and deduplicate *before*
    forwarding an alert to any paper decision workflow.
    """
    if len(raw) > 4096:
        raise AlertRejected("payload too large")
    if received_time.tzinfo is None or received_time.utcoffset() is None:
        raise AlertRejected("received_time must be timezone-aware")
    if max_age <= timedelta(0):
        raise AlertRejected("max_age must be positive")
    try:
        payload = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise AlertRejected("invalid JSON") from exc
    required = {"alert_id", "strategy", "exchange", "symbol", "direction", "event_time", "reference_price"}
    if not isinstance(payload, dict) or set(payload) != required:
        raise AlertRejected("unexpected or missing fields")
    for key in ("alert_id", "strategy", "exchange", "symbol", "direction", "event_time"):
        if not isinstance(payload[key], str) or not payload[key]:
            raise AlertRejected(f"invalid {key}")
    if len(payload["alert_id"]) > 128 or len(payload["symbol"]) > 64:
        raise AlertRejected("identifier too long")
    if payload["strategy"] not in allowed_strategies:
        raise AlertRejected("strategy is not allowlisted")
    if payload["exchange"] not in {"NSE", "BSE", "NFO", "BFO"}:
        raise AlertRejected("unsupported exchange")
    if payload["direction"] not in {"UP", "DOWN"}:
        raise AlertRejected("direction must be UP or DOWN, not an order instruction")
    try:
        event_time = datetime.fromisoformat(payload["event_time"].replace("Z", "+00:00"))
    except ValueError as exc:
        raise AlertRejected("invalid event_time") from exc
    if event_time.tzinfo is None or event_time.utcoffset() is None:
        raise AlertRejected("event_time must be timezone-aware")
    age = received_time.astimezone(timezone.utc) - event_time.astimezone(timezone.utc)
    if age > max_age or age < -timedelta(seconds=5):
        raise AlertRejected("stale or future alert")
    price = payload["reference_price"]
    if isinstance(price, bool) or not isinstance(price, (int, float)) or not math.isfinite(price) or price <= 0:
        raise AlertRejected("invalid reference_price")
    return TradingViewAlert(payload["alert_id"], payload["strategy"], payload["exchange"],
                            payload["symbol"], payload["direction"], event_time,
                            received_time, float(price))
