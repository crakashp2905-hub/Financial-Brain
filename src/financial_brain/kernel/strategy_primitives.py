"""Strategy primitives and declarations (FB-0126, FB-0127, FB-0128, FB-0136).

* Operators on aligned series. Any comparison involving a missing value is missing (never True).
* Crossover and breakout use only earlier bars, so a decision at bar i never uses bar i+1.
* ``IntentDeclaration`` validates time-in-force, bracket and trailing settings, and maps to a plain OMS intent dict.
  Unknown fields are refused rather than ignored.
* Templates build signals from the primitives: momentum, mean reversion, pair spread and pattern breakout.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from statistics import mean, pstdev

Series = Sequence[float | None]
TIF = {"DAY", "IOC", "FOK", "GTC"}


def _binary(a: Series, b: Series, op: str) -> list[bool | None]:
    if len(a) != len(b):
        raise ValueError("series must be aligned")
    out: list[bool | None] = []
    for x, y in zip(a, b, strict=True):
        if x is None or y is None:
            out.append(None)
        else:
            out.append(x > y if op == ">" else x < y)
    return out


def greater(a: Series, b: Series) -> list[bool | None]:
    return _binary(a, b, ">")


def less(a: Series, b: Series) -> list[bool | None]:
    return _binary(a, b, "<")


def crossover(a: Series, b: Series) -> list[bool]:
    """True at bar i when a was at or below b at i-1 and is above b at i. Missing data never triggers."""
    if len(a) != len(b):
        raise ValueError("series must be aligned")
    out = [False] * len(a)
    for i in range(1, len(a)):
        p, c = a[i - 1], a[i]
        q, d = b[i - 1], b[i]
        if None in (p, c, q, d):
            continue
        assert p is not None and c is not None and q is not None and d is not None
        out[i] = p <= q and c > d
    return out


def breakout(close: Series, high: Series, window: int) -> list[bool]:
    """Close above the highest high of the previous ``window`` bars (current bar excluded)."""
    if window < 1:
        raise ValueError("window must be positive")
    out = [False] * len(close)
    for i in range(window, len(close)):
        prior = [v for v in high[i - window : i] if v is not None]
        current = close[i]
        if current is None or len(prior) < window:
            continue
        out[i] = current > max(prior)
    return out


def zscore_prior(x: Series, window: int) -> list[float | None]:
    """z-score of bar i against the previous ``window`` bars only."""
    out: list[float | None] = [None] * len(x)
    for i in range(window, len(x)):
        prior = [v for v in x[i - window : i] if v is not None]
        if len(prior) < window or x[i] is None:
            continue
        sd = pstdev(prior)
        out[i] = 0.0 if sd == 0 else (x[i] - mean(prior)) / sd  # type: ignore[operator]
    return out


def spread(a: Series, b: Series, hedge: float) -> list[float | None]:
    if len(a) != len(b):
        raise ValueError("series must be aligned")
    return [None if x is None or y is None else x - hedge * y for x, y in zip(a, b, strict=True)]


@dataclass(frozen=True)
class IntentDeclaration:
    strategy: str
    instrument: str
    side: str
    quantity: int
    entry: Decimal | None = None
    time_in_force: str = "DAY"
    stop: Decimal | None = None
    target: Decimal | None = None
    trailing_pct: Decimal | None = None

    def __post_init__(self) -> None:
        if self.side not in ("BUY", "SELL"):
            raise ValueError("side must be BUY or SELL")
        if self.quantity <= 0:
            raise ValueError("quantity must be positive")
        if self.time_in_force not in TIF:
            raise ValueError(f"unknown time_in_force {self.time_in_force!r}")
        if self.stop is not None or self.target is not None:
            if self.entry is None:
                raise ValueError("bracket needs an entry price")
            if self.side == "BUY":
                if self.stop is not None and self.stop >= self.entry:
                    raise ValueError("BUY stop must be below entry")
                if self.target is not None and self.target <= self.entry:
                    raise ValueError("BUY target must be above entry")
            else:
                if self.stop is not None and self.stop <= self.entry:
                    raise ValueError("SELL stop must be above entry")
                if self.target is not None and self.target >= self.entry:
                    raise ValueError("SELL target must be below entry")
        if self.trailing_pct is not None and not (0 < self.trailing_pct < 1):
            raise ValueError("trailing_pct must be between 0 and 1")

    def to_oms_intent(self) -> dict[str, object]:
        return {
            "strategy": self.strategy,
            "instrument": self.instrument,
            "side": self.side,
            "quantity": self.quantity,
            "time_in_force": self.time_in_force,
            "limit": self.entry,
            "bracket": {"stop": self.stop, "target": self.target} if (self.stop or self.target) else None,
            "trailing_pct": self.trailing_pct,
        }


def momentum_template(close: Series, lookback: int) -> list[bool]:
    """Long while close is above its close ``lookback`` bars ago; applied from the next bar."""
    raw = [False] * len(close)
    for i in range(lookback, len(close)):
        if close[i] is not None and close[i - lookback] is not None:
            raw[i] = close[i] > close[i - lookback]  # type: ignore[operator]
    return [False] + raw[:-1]


def mean_reversion_template(x: Series, window: int, entry_z: float) -> list[bool]:
    z = zscore_prior(x, window)
    raw = [v is not None and v <= -entry_z for v in z]
    return [False] + raw[:-1]


def pair_spread_template(a: Series, b: Series, hedge: float, window: int, entry_z: float) -> list[bool]:
    sp = spread(a, b, hedge)
    z = zscore_prior(sp, window)
    raw = [v is not None and v <= -entry_z for v in z]
    return [False] + raw[:-1]


def pattern_breakout_template(close: Series, high: Series, window: int) -> list[bool]:
    raw = breakout(close, high, window)
    return [False] + raw[:-1]
