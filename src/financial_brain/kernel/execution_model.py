"""Execution model pieces (FB-0191, FB-0192, FB-0198, FB-0200, FB-0205).

* ``queue_fill_estimate`` is a rough queue-position model. Every result is labelled ``ESTIMATED``, never measured.
* ``within_price_band`` refuses a price further than the band from the reference.
* ``fx_all_in_cost`` adds spread, commission and swap over the holding period. Swap may be negative (a credit).
* ``multileg_status`` reports ALL_FILLED, NONE or PARTIAL. PARTIAL carries a legging-risk action, never a silent pass.
* ``pessimistic_fill`` widens slippage by a fixed multiplier when the market is not calibrated.
"""
from __future__ import annotations

from decimal import Decimal


def queue_fill_estimate(queue_ahead: int, volume_at_level: int) -> dict[str, object]:
    """Share of the queue that must trade before our order fills. Labelled ESTIMATED."""
    if queue_ahead < 0 or volume_at_level < 0:
        raise ValueError("queue and volume must be non-negative")
    if volume_at_level == 0:
        p = 0.0
    else:
        p = max(0.0, 1.0 - queue_ahead / volume_at_level)
    return {"fill_probability": p, "label": "ESTIMATED"}


def within_price_band(price: Decimal, reference: Decimal, band_pct: Decimal) -> bool:
    if reference <= 0 or band_pct <= 0:
        raise ValueError("reference and band must be positive")
    return abs(price - reference) / reference <= band_pct


def fx_all_in_cost(
    notional: Decimal,
    spread_pips: Decimal,
    pip_value: Decimal,
    commission: Decimal,
    swap_per_day: Decimal,
    days: int,
) -> Decimal:
    """Total cost. A positive swap_per_day is a charge, a negative one is a credit."""
    if days < 0:
        raise ValueError("days must be non-negative")
    spread_cost = spread_pips * pip_value
    return spread_cost + commission + swap_per_day * Decimal(days)


def multileg_status(leg_fills: list[Decimal], leg_sizes: list[Decimal]) -> str:
    if len(leg_fills) != len(leg_sizes):
        raise ValueError("fills and sizes must align")
    if all(f == 0 for f in leg_fills):
        return "NONE"
    if all(f >= s for f, s in zip(leg_fills, leg_sizes, strict=True)):
        return "ALL_FILLED"
    return "PARTIAL"


def pessimistic_fill(price: Decimal, side: str, base_slippage_bps: Decimal, calibrated: bool) -> Decimal:
    """Uncalibrated markets get three times the base slippage, always adverse to the side."""
    multiplier = Decimal(1) if calibrated else Decimal(3)
    slip = price * base_slippage_bps * multiplier / Decimal(10000)
    if side == "BUY":
        return price + slip
    if side == "SELL":
        return price - slip
    raise ValueError("side must be BUY or SELL")
