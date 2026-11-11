"""FX and currency model (FB-0333, FB-0334, FB-0335, FB-0336, FB-0337, FB-0338, FB-0340, FB-0342, FB-0345, FB-0346,
FB-0347).

* ``CurrencyPair`` validates base and quote codes and gives the pip size (0.01 for JPY quotes, else 0.0001).
* ``pipette`` is one tenth of a pip. ``notional`` is units times price, converted to account currency.
* ``convert_pnl`` converts quote-currency P&L to account currency at a given rate.
* ``stress_fill`` fills a buy above the mid and a sell below it, by a spread multiplier.
* ``rollover_window`` is True inside the assumed 21:00-22:00 UTC rollover window (a documented assumption).
* ``weekend_gap`` flags a Friday-to-Monday move larger than 1%.
* ``carry_signal`` is base rate minus quote rate minus swap cost, with its sign.
* ``events_between`` filters offline economic-calendar fixtures by time, inclusive.
* ``pips`` is signed pip movement for a side. ``hedge_units`` sizes a hedge, rounding toward zero.
* ``candle_from_ticks`` builds an OHLC candle from ticks; a synthetic candle agrees with the tick high and low.
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN

_CODE = re.compile(r"^[A-Z]{3}$")


@dataclass(frozen=True)
class CurrencyPair:
    base: str
    quote: str

    def __post_init__(self) -> None:
        if not (_CODE.match(self.base) and _CODE.match(self.quote)):
            raise ValueError("currency codes must be three upper-case letters")
        if self.base == self.quote:
            raise ValueError("base and quote must differ")

    @property
    def pip_size(self) -> Decimal:
        return Decimal("0.01") if self.quote == "JPY" else Decimal("0.0001")


def pipette(pair: CurrencyPair) -> Decimal:
    return pair.pip_size / Decimal(10)


def notional(units: int, price: Decimal, quote_to_account: Decimal = Decimal(1)) -> Decimal:
    return Decimal(units) * price * quote_to_account


def convert_pnl(pnl_quote: Decimal, quote_to_account: Decimal) -> Decimal:
    if quote_to_account <= 0:
        raise ValueError("rate must be positive")
    return pnl_quote * quote_to_account


def stress_fill(side: str, mid: Decimal, half_spread: Decimal, multiplier: Decimal) -> Decimal:
    if multiplier < 1:
        raise ValueError("multiplier must be at least 1")
    widened = half_spread * multiplier
    if side == "BUY":
        return mid + widened
    if side == "SELL":
        return mid - widened
    raise ValueError("side must be BUY or SELL")


def rollover_window(hour_utc: int) -> bool:
    if not 0 <= hour_utc <= 23:
        raise ValueError("hour must be 0-23")
    return 21 <= hour_utc < 22


def weekend_gap(friday_close: Decimal, monday_open: Decimal) -> tuple[Decimal, bool]:
    if friday_close <= 0:
        raise ValueError("friday close must be positive")
    pct = (monday_open - friday_close) / friday_close
    return pct, abs(pct) > Decimal("0.01")


def carry_signal(rate_base: Decimal, rate_quote: Decimal, swap_cost: Decimal) -> dict[str, Decimal | int]:
    net = rate_base - rate_quote - swap_cost
    sign = 1 if net > 0 else -1 if net < 0 else 0
    return {"net_carry": net, "sign": sign}


def events_between(fixtures: Sequence[tuple[int, str]], start: int, end: int) -> list[tuple[int, str]]:
    return [e for e in fixtures if start <= e[0] <= end]


def pips(entry: Decimal, exit_: Decimal, pip: Decimal, side: str) -> Decimal:
    if side == "BUY":
        return (exit_ - entry) / pip
    if side == "SELL":
        return (entry - exit_) / pip
    raise ValueError("side must be BUY or SELL")


def hedge_units(exposure_units: int, hedge_ratio: Decimal) -> int:
    if not (Decimal(0) <= hedge_ratio <= Decimal(1)):
        raise ValueError("hedge ratio must be in [0, 1]")
    return int((Decimal(exposure_units) * hedge_ratio).to_integral_value(rounding=ROUND_DOWN))


def candle_from_ticks(prices: Sequence[Decimal]) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    if not prices:
        raise ValueError("no ticks")
    return prices[0], max(prices), min(prices), prices[-1]
