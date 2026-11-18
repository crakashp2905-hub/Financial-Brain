"""Immutable comparison of captured live trades with later historical candles (PE-0043)."""
from __future__ import annotations

import hashlib
import json
from datetime import timedelta, timezone
from decimal import Decimal, InvalidOperation

from financial_brain.forward_edge.durable import DurableJournal
from financial_brain.forward_edge.market_data import NormalizedEvent


class DivergenceError(ValueError):
    """Invalid or non-point-in-time market-data comparison input."""


_INTERVAL_MINUTES = {"minute": 1, "3minute": 3, "5minute": 5, "10minute": 10,
                     "15minute": 15, "30minute": 30, "60minute": 60}


def compare_live_ticks_to_historical_bar(*, ticks: list[NormalizedEvent], bar: NormalizedEvent,
                                         live_source: str, tolerance_bps: Decimal,
                                         audit_journal: DurableJournal) -> dict:
    """Compare tick-derived OHLC to a later authorized historical bar and durably log the result.

    The function never repairs or replaces either dataset. `live_source` and the
    `kite_historical` source tag are recorded for audit; callers must obtain these
    inputs through a rights-gated capture/replay path.
    """
    if bar.kind != "BAR":
        raise DivergenceError("historical comparison requires a BAR event")
    payload = dict(bar.payload)
    if payload.get("source") != "kite_historical":
        raise DivergenceError("historical bar must carry the Kite historical source tag")
    interval = payload.get("interval")
    if interval not in _INTERVAL_MINUTES:
        raise DivergenceError("bar interval is missing or unsupported")
    if not live_source or not live_source.strip():
        raise DivergenceError("live tick source must be identified")
    if not isinstance(tolerance_bps, Decimal) or not tolerance_bps.is_finite() or tolerance_bps < 0:
        raise DivergenceError("tolerance_bps must be finite and non-negative")
    bar_time = bar.event_utc.astimezone(timezone.utc)
    if bar.available_utc.date() <= bar_time.date():
        raise DivergenceError("historical bar must be retrieved on a later UTC date than its market event")
    end_time = bar.event_utc + timedelta(minutes=_INTERVAL_MINUTES[interval])
    selected = [event for event in ticks if event.kind == "TICK" and event.instrument == bar.instrument
                and bar.event_utc <= event.event_utc < end_time]
    selected.sort(key=lambda event: (event.event_utc, event.content_sha256()))
    if selected and bar.available_utc <= max(event.available_utc for event in selected):
        raise DivergenceError("historical bar availability must follow all compared live ticks")

    report: dict = {
        "instrument": bar.instrument,
        "bar_event_sha256": bar.content_sha256(),
        "tick_event_sha256s": [event.content_sha256() for event in selected],
        "live_source": live_source,
        "historical_source": "kite_historical",
        "interval": interval,
        "tolerance_bps": str(tolerance_bps),
        "tick_count": len(selected),
        "comparison_status": "INSUFFICIENT_DATA",
        "verification_status": "UNVERIFIED",
        "adjustment_applied": False,
        "metrics": {},
    }
    if selected:
        prices = [_tick_price(event) for event in selected]
        if all(price is not None for price in prices):
            valid_prices = [price for price in prices if price is not None]
            live_ohlc = {"open": valid_prices[0], "high": max(valid_prices),
                         "low": min(valid_prices), "close": valid_prices[-1]}
            metric_rows = {}
            divergent = False
            for key, live_value in live_ohlc.items():
                try:
                    historical_value = Decimal(payload[key])
                except (KeyError, InvalidOperation) as exc:
                    raise DivergenceError(f"historical bar is missing valid {key}") from exc
                if not historical_value.is_finite() or historical_value <= 0:
                    raise DivergenceError(f"historical bar {key} must be positive and finite")
                delta = live_value - historical_value
                delta_bps = abs(delta) / historical_value * Decimal(10_000)
                metric_rows[key] = {"live_tick_value": str(live_value),
                                    "historical_bar_value": str(historical_value),
                                    "delta": str(delta), "absolute_delta_bps": str(delta_bps)}
                divergent = divergent or delta_bps > tolerance_bps
            report["metrics"] = metric_rows
            report["comparison_status"] = "DIVERGENT" if divergent else "MATCH_WITHIN_TOLERANCE"
    canonical = json.dumps(report, sort_keys=True, separators=(",", ":"), allow_nan=False)
    comparison_id = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    report["comparison_id"] = comparison_id
    prior = next((row for row in audit_journal.records()
                  if row.get("t") == "LIVE_HISTORICAL_DIVERGENCE"
                  and row.get("comparison_id") == comparison_id), None)
    if prior is None:
        audit_journal.append({"t": "LIVE_HISTORICAL_DIVERGENCE", **report})
    elif {key: value for key, value in prior.items() if key != "t"} != report:
        raise DivergenceError("comparison ID collides with different durable audit evidence")
    return report


def _tick_price(event: NormalizedEvent) -> Decimal | None:
    payload = dict(event.payload)
    try:
        price = Decimal(payload.get("price", payload.get("last_price", "")))
    except InvalidOperation:
        return None
    return price if price.is_finite() and price > 0 else None
