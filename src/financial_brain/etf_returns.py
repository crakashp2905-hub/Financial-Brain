"""Keep ETF market-price, NAV and underlying benchmark returns as separate series."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Iterable


@dataclass(frozen=True)
class ETFReturnSeries:
    label: str
    start_date: date
    end_date: date
    start_value: Decimal
    end_value: Decimal
    return_fraction: Decimal
    benchmark_return_type: str | None = None


def _series(label: str, observations: Iterable[tuple[date, Decimal | int | float | str]],
            benchmark_return_type: str | None = None) -> ETFReturnSeries:
    rows = list(observations)
    if len(rows) < 2:
        raise ValueError(f"{label} requires at least two dated observations")
    if any(not isinstance(d, date) for d, _ in rows):
        raise ValueError(f"{label} dates must be date values")
    if [d for d, _ in rows] != sorted({d for d, _ in rows}):
        raise ValueError(f"{label} dates must be unique and ascending")
    try:
        values = [Decimal(str(value)) for _, value in rows]
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{label} values must be finite positive numbers") from exc
    if any(not value.is_finite() or value <= 0 for value in values):
        raise ValueError(f"{label} values must be finite positive numbers")
    return ETFReturnSeries(label, rows[0][0], rows[-1][0], values[0], values[-1],
                           values[-1] / values[0] - Decimal(1), benchmark_return_type)


def separate_returns(*, market_price: Iterable[tuple[date, Decimal | int | float | str]],
                      nav: Iterable[tuple[date, Decimal | int | float | str]],
                      underlying: Iterable[tuple[date, Decimal | int | float | str]],
                      benchmark_return_type: str) -> tuple[ETFReturnSeries, ...]:
    """Calculate separately labelled returns over one common observed date window.

    Inputs must already represent the chosen return convention. In particular callers must
    provide the ETF's market-price series, the ETF NAV series, and the benchmark series
    independently; this function never fills one series from another.
    """
    kind = benchmark_return_type.strip().upper()
    if kind not in {"TRI", "PRICE", "OTHER", "UNKNOWN"}:
        raise ValueError("benchmark_return_type must be TRI, PRICE, OTHER or UNKNOWN")
    price = _series("ETF_MARKET_PRICE_RETURN", market_price)
    nav_return = _series("ETF_NAV_RETURN", nav)
    underlying_label = "UNDERLYING_TRI_RETURN" if kind == "TRI" else f"UNDERLYING_{kind}_RETURN"
    benchmark = _series(underlying_label, underlying, kind)
    windows = {(item.start_date, item.end_date) for item in (price, nav_return, benchmark)}
    if len(windows) != 1:
        raise ValueError("market price, NAV and underlying series must share the same date window")
    return price, nav_return, benchmark
