"""Observed quote/depth impact summaries grouped by instrument, session, size and participation.

The estimator describes displayed-book walking cost. It is not a realized-fill model and
will not claim calibration until populated with authorized observed events.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from statistics import median
from typing import Any

from financial_brain.forward_edge.market_events import EventKind
from financial_brain.forward_edge.quote_archive import QuoteArchive


class ImpactCalibrationError(ValueError):
    """Insufficient or invalid observed quote evidence for impact estimation."""


@dataclass(frozen=True)
class ImpactBucket:
    instrument_id: str
    session_date: date
    quantity: int
    participation_bucket: str
    observations: int
    median_round_trip_bps: float
    p90_round_trip_bps: float
    source_classes: tuple[str, ...]
    event_hashes: tuple[str, ...]
    calibrated: bool


def _walk(levels: Any, quantity: int) -> float:
    if not isinstance(levels, (list, tuple)):
        raise ImpactCalibrationError("quote has no order-book depth")
    remaining, notional = quantity, 0.0
    for row in levels:
        if isinstance(row, dict):
            price, size = float(row["price"]), int(row["quantity"])
        elif isinstance(row, (list, tuple)) and len(row) == 2:
            price, size = float(row[0]), int(row[1])
        else:
            raise ImpactCalibrationError("malformed order-book level")
        if price <= 0 or size <= 0:
            raise ImpactCalibrationError("order-book levels must be positive")
        take = min(remaining, size)
        notional += price * take
        remaining -= take
        if not remaining:
            break
    if remaining:
        raise ImpactCalibrationError("displayed depth cannot fill requested size")
    return notional / quantity


def estimate_displayed_impact(archive: QuoteArchive, *, instrument_id: str, session_date: date,
                              quantities: tuple[int, ...], participation_bucket: str,
                              minimum_observations: int = 20) -> tuple[ImpactBucket, ...]:
    """Summarize two-sided displayed-book round-trip costs for explicit order sizes.

    Participation is an explicit caller label because minute volume is not included in every
    normalized quote. Only valid positive-volume quotes with complete depth are considered.
    Percentiles use nearest-rank p90. Hashes pin the exact source events used.
    """
    if not instrument_id.strip() or not participation_bucket.strip():
        raise ImpactCalibrationError("instrument and participation bucket are required")
    if not quantities or any(type(value) is not int or value < 1 for value in quantities):
        raise ImpactCalibrationError("one or more positive integer order quantities are required")
    if type(minimum_observations) is not int or minimum_observations < 2:
        raise ImpactCalibrationError("minimum observations must be at least two")
    events = [event for event in archive.events_for_session(session_date, venue="NSE")
              if event.instrument_id == instrument_id and event.kind is EventKind.QUOTE]
    usable = []
    for event in events:
        payload = event.payload
        try:
            bid, ask = float(payload["bid"]), float(payload["ask"])
            volume = float(payload["volume"])
            if not (0 < bid < ask and volume > 0):
                continue
            bids = payload.get("bid_levels") or (payload.get("depth") or {}).get("buy")
            asks = payload.get("ask_levels") or (payload.get("depth") or {}).get("sell")
            usable.append((event, bid, ask, bids, asks))
        except (KeyError, TypeError, ValueError):
            continue

    results = []
    for quantity in sorted(set(quantities)):
        costs: list[float] = []
        hashes: list[str] = []
        sources: set[str] = set()
        for event, bid, ask, bids, asks in usable:
            try:
                buy_vwap, sell_vwap = _walk(asks, quantity), _walk(bids, quantity)
            except (ImpactCalibrationError, KeyError, TypeError, ValueError, OverflowError):
                continue
            midpoint = (ask + bid) / 2
            costs.append(((buy_vwap - midpoint) + (midpoint - sell_vwap)) / midpoint * 10_000)
            hashes.append(event.sha256())
            sources.add(event.source)
        if len(costs) < minimum_observations:
            raise ImpactCalibrationError(
                f"{instrument_id} {session_date} quantity={quantity}: {len(costs)} usable observations; "
                f"{minimum_observations} required")
        ordered = sorted(costs)
        p90 = ordered[max(0, (9 * len(ordered) + 9) // 10 - 1)]
        calibrated = bool(sources) and all(
            source.lower().startswith(("kite:", "broker:", "exchange:")) for source in sources)
        results.append(ImpactBucket(instrument_id, session_date, quantity, participation_bucket,
                                    len(costs), median(costs), p90, tuple(sorted(sources)),
                                    tuple(hashes), calibrated))
    return tuple(results)
