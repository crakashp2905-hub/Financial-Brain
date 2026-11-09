"""Causal indicators (FB-0103, FB-0105, FB-0106, FB-0107 subset).

Every value at index ``i`` uses only bars ``0..i``, so truncating the series after ``i`` never changes it. Donchian
channels exclude the current bar, so a breakout is compared with the prior range. Values before the warm-up
window are ``None``, never a made-up number (FB-0118).
"""
from __future__ import annotations

from collections.abc import Sequence


def sma(values: Sequence[float], n: int) -> list[float | None]:
    if n < 1:
        raise ValueError("window must be positive")
    out: list[float | None] = []
    running = 0.0
    for i, v in enumerate(values):
        running += v
        if i >= n:
            running -= values[i - n]
        out.append(running / n if i >= n - 1 else None)
    return out


def ema(values: Sequence[float], n: int) -> list[float | None]:
    if n < 1:
        raise ValueError("window must be positive")
    alpha = 2.0 / (n + 1)
    out: list[float | None] = []
    prev: float | None = None
    for i, v in enumerate(values):
        if i < n - 1:
            out.append(None)
            continue
        if prev is None:
            prev = sum(values[:n]) / n
        else:
            prev = alpha * v + (1 - alpha) * prev
        out.append(prev)
    return out


def true_range(high: Sequence[float], low: Sequence[float], close: Sequence[float]) -> list[float]:
    out = [high[0] - low[0]]
    for i in range(1, len(close)):
        out.append(max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1])))
    return out


def atr(high: Sequence[float], low: Sequence[float], close: Sequence[float], n: int) -> list[float | None]:
    """Wilder ATR, seeded with the mean of the first n true ranges."""
    tr = true_range(high, low, close)
    out: list[float | None] = [None] * len(tr)
    if len(tr) < n:
        return out
    prev = sum(tr[:n]) / n
    out[n - 1] = prev
    for i in range(n, len(tr)):
        prev = (prev * (n - 1) + tr[i]) / n
        out[i] = prev
    return out


def donchian_prior(high: Sequence[float], low: Sequence[float], n: int) -> list[tuple[float, float] | None]:
    """Channel from the prior n bars, excluding bar i. Breakouts compare close_i with this, never with itself."""
    out: list[tuple[float, float] | None] = []
    for i in range(len(high)):
        if i < n:
            out.append(None)
        else:
            out.append((max(high[i - n:i]), min(low[i - n:i])))
    return out


def rsi(values: Sequence[float], n: int) -> list[float | None]:
    """Wilder RSI. A window with no losses returns 100; no gains and no losses returns 50."""
    out: list[float | None] = [None] * len(values)
    if len(values) <= n:
        return out
    gains = [max(values[i] - values[i - 1], 0.0) for i in range(1, len(values))]
    losses = [max(values[i - 1] - values[i], 0.0) for i in range(1, len(values))]
    ag, al = sum(gains[:n]) / n, sum(losses[:n]) / n

    def value(g: float, l: float) -> float:
        if l == 0:
            return 50.0 if g == 0 else 100.0
        return 100.0 - 100.0 / (1.0 + g / l)

    out[n] = value(ag, al)
    for i in range(n, len(gains)):
        ag = (ag * (n - 1) + gains[i]) / n
        al = (al * (n - 1) + losses[i]) / n
        out[i + 1] = value(ag, al)
    return out


def session_vwap(prices: Sequence[float], volumes: Sequence[float], session_ids: Sequence[str]) -> list[float | None]:
    """VWAP that resets at each session boundary. Zero-volume sessions give None, not zero."""
    out: list[float | None] = []
    cur, pv, vv = None, 0.0, 0.0
    for p, v, s in zip(prices, volumes, session_ids, strict=True):
        if s != cur:
            cur, pv, vv = s, 0.0, 0.0
        pv += p * v
        vv += v
        out.append(pv / vv if vv > 0 else None)
    return out
