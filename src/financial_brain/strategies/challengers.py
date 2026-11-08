"""Research-only challengers: Keltner volatility expansion with EMA, ADX and volume filters (ALG-051) and a combined
RSI/Keltner/MACD concept (ALG-067).

Both are frozen grids. Their outputs are challenger signals to be scored against baselines, never recommendations
and never edge claims. Every feature uses only bars before the decision bar.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from itertools import product

from financial_brain.strategies.specs import decision_inputs_ok

KELTNER_GRID = {"ema": (20,), "atr": (10,), "mult": (1.5, 2.0), "adx_min": (20.0,), "volume_mult": (1.0,)}  # frozen
TRIALS_IN_GRID = len(list(product(*KELTNER_GRID.values())))  # counted once in the research ledger


def ema(values: Sequence[float], period: int) -> list[float | None]:
    out: list[float | None] = []
    alpha = 2 / (period + 1)
    prev: float | None = None
    for v in values:
        if decision_inputs_ok([v]) != "OK":
            out.append(None)
            continue
        prev = v if prev is None else alpha * v + (1 - alpha) * prev
        out.append(prev)
    return out


def true_ranges(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float]) -> list[float]:
    trs = [highs[0] - lows[0]]
    for i in range(1, len(closes)):
        trs.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))
    return trs


def adx(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], period: int = 14) -> list[float | None]:
    """Average directional index, computed from bars up to each index only."""
    n = len(closes)
    out: list[float | None] = [None] * n
    if n < 2 * period + 1:
        return out
    tr = true_ranges(highs, lows, closes)
    plus_dm = [0.0] + [max(highs[i] - highs[i - 1], 0.0) if highs[i] - highs[i - 1] > lows[i - 1] - lows[i] else 0.0 for i in range(1, n)]
    minus_dm = [0.0] + [max(lows[i - 1] - lows[i], 0.0) if lows[i - 1] - lows[i] > highs[i] - highs[i - 1] else 0.0 for i in range(1, n)]
    dx_vals: list[float] = []
    for i in range(period, n):
        atr_i = sum(tr[i - period + 1 : i + 1])
        if atr_i <= 0:
            continue
        pdi = 100 * sum(plus_dm[i - period + 1 : i + 1]) / atr_i
        mdi = 100 * sum(minus_dm[i - period + 1 : i + 1]) / atr_i
        denom = pdi + mdi
        dx = 100 * abs(pdi - mdi) / denom if denom > 0 else 0.0
        dx_vals.append(dx)
        if len(dx_vals) >= period:
            out[i] = sum(dx_vals[-period:]) / period
    return out


@dataclass(frozen=True)
class KeltnerSignal:
    index: int
    side: str


def keltner_breakouts(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], volumes: Sequence[float],
                      *, ema_period: int = 20, atr_period: int = 10, mult: float = 2.0, adx_min: float = 20.0,
                      volume_mult: float = 1.0) -> list[KeltnerSignal]:
    """Long when the close breaks above EMA + mult*ATR, ADX is trending and volume is above its lagged average.

    Thresholds at bar t use the EMA and ATR of bars before t, so bar t's own range cannot set its own trigger.
    """
    mids = ema(closes, ema_period)
    tr = true_ranges(highs, lows, closes)
    trend = adx(highs, lows, closes)
    out: list[KeltnerSignal] = []
    for t in range(max(ema_period, atr_period) + 1, len(closes)):
        m = mids[t - 1]
        if m is None:
            continue
        atr_prev = sum(tr[t - atr_period : t]) / atr_period
        upper = m + mult * atr_prev
        a = trend[t]
        vol_avg = sum(volumes[t - 20 : t]) / 20 if t >= 20 else None
        if a is None or vol_avg is None or vol_avg <= 0:
            continue
        if closes[t] > upper and a >= adx_min and volumes[t] >= volume_mult * vol_avg:
            out.append(KeltnerSignal(t, "LONG"))
    return out


def rsi(closes: Sequence[float], period: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(closes)
    for t in range(period + 1, len(closes)):
        gains = [max(closes[k] - closes[k - 1], 0.0) for k in range(t - period + 1, t + 1)]
        losses = [max(closes[k - 1] - closes[k], 0.0) for k in range(t - period + 1, t + 1)]
        avg_g, avg_l = sum(gains) / period, sum(losses) / period
        out[t] = 100.0 if avg_l == 0 else 100 - 100 / (1 + avg_g / avg_l)
    return out


def macd_histogram(closes: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9) -> list[float | None]:
    f = ema(closes, fast)
    s = ema(closes, slow)
    line = [None if a is None or b is None else a - b for a, b in zip(f, s, strict=True)]
    clean = [0.0 if v is None else v for v in line]
    sig = ema(clean, signal)
    return [None if (l_ is None or sg is None) else l_ - sg for l_, sg in zip(line, sig, strict=True)]


def combined_challenger(closes: Sequence[float], highs: Sequence[float], lows: Sequence[float]) -> list[int]:
    """Research-only combination (ALG-067): RSI not overbought, MACD histogram rising, close above Keltner midline.

    Output is a list of challenger bar indices. It is scored against cash and momentum; it is never promoted.
    """
    r = rsi(closes)
    h = macd_histogram(closes)
    mids = ema(closes, 20)
    out: list[int] = []
    for t in range(30, len(closes)):
        rt, ht, ht1, m = r[t], h[t], h[t - 1], mids[t]
        if rt is None or ht is None or ht1 is None or m is None:
            continue
        if rt < 70 and ht > ht1 and ht > 0 and closes[t] > m:
            out.append(t)
    return out
