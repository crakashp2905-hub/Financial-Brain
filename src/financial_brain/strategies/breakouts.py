"""Trend breakout families: Donchian, Turtle-style 20/10, ATR-threshold, two-break confirmation, gap-aware fills,
ATR-N sizing and a negative-control trend filter (ALG-044..053).

Every level at bar ``t`` uses bars strictly before ``t`` unless stated. Long-only signals suit cash equity; short
signals need a futures segment and are not produced here for cash. Inputs are plain sequences; missing or
non-finite values abstain.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from financial_brain.strategies.specs import decision_inputs_ok


@dataclass(frozen=True)
class Signal:
    index: int
    side: str
    reference_level: float
    rule: str


def donchian_upper(highs: Sequence[float], t: int, n: int) -> float | None:
    """Highest high of the ``n`` bars before ``t``; never includes bar ``t`` (ALG-044)."""
    if n < 1 or t < n or t > len(highs):
        return None
    window = highs[t - n : t]
    if decision_inputs_ok(list(window)) != "OK":
        return None
    return max(window)


def donchian_lower(lows: Sequence[float], t: int, n: int) -> float | None:
    if n < 1 or t < n or t > len(lows):
        return None
    window = lows[t - n : t]
    if decision_inputs_ok(list(window)) != "OK":
        return None
    return min(window)


def basic_breakouts(closes: Sequence[float], highs: Sequence[float], n: int = 20) -> list[Signal]:
    """Long breakout when the close exceeds the prior ``n``-bar high (ALG-045). Cash-long only."""
    out: list[Signal] = []
    for t in range(n, len(closes)):
        if decision_inputs_ok([closes[t]]) != "OK":
            continue
        upper = donchian_upper(highs, t, n)
        if upper is None:
            continue
        if closes[t] > upper:
            out.append(Signal(t, "LONG", upper, f"donchian_{n}"))
    return out


TURTLE_ENTRY = 20  # ALG-046: fixed Turtle-style rules, not tuned crypto defaults
TURTLE_EXIT = 10


def turtle_exit_level(lows: Sequence[float], t: int, exit_n: int = TURTLE_EXIT) -> float | None:
    """Exit reference: lowest low of the ``exit_n`` bars before ``t`` (long positions)."""
    return donchian_lower(lows, t, exit_n)


def atr_past_only(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], t: int, period: int = 14) -> float | None:
    """ATR using bars strictly before ``t`` (ALG-050): uses no information from bar ``t`` or later."""
    if t < period + 1 or t > len(closes):
        return None
    trs: list[float] = []
    for k in range(t - period, t):
        if decision_inputs_ok([highs[k], lows[k], closes[k - 1]]) != "OK":
            return None
        trs.append(max(highs[k] - lows[k], abs(highs[k] - closes[k - 1]), abs(lows[k] - closes[k - 1])))
    value = sum(trs) / period
    return value if value > 0 else None


def atr_threshold_trigger(
    highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], t: int, k: float, prior_high: float,
    period: int = 14,
) -> bool:
    """True when the close exceeds ``prior_high + k * ATR`` where ATR is computed from past bars only (ALG-050)."""
    atr = atr_past_only(highs, lows, closes, t, period)
    if atr is None or not math.isfinite(k) or k < 0:
        return False
    return closes[t] > prior_high + k * atr


def two_break_confirmation(closes: Sequence[float], highs: Sequence[float], n: int = 20) -> list[Signal]:
    """Two consecutive closes above the prior channel high (ALG-048). A separate hypothesis from the basic breakout."""
    out: list[Signal] = []
    for t in range(n + 1, len(closes)):
        upper_prev = donchian_upper(highs, t - 1, n)
        upper_now = donchian_upper(highs, t, n)
        if upper_prev is None or upper_now is None:
            continue
        if closes[t - 1] > upper_prev and closes[t] > upper_now:
            out.append(Signal(t, "LONG", upper_now, f"two_break_donchian_{n}"))
    return out


def gap_aware_fill(prev_close: float, open_: float, trigger: float) -> float | None:
    """Fill rule for a breakout trigger (ALG-052): a gap through the trigger fills at the open, not the trigger."""
    if decision_inputs_ok([prev_close, open_, trigger]) != "OK":
        return None
    return open_ if open_ > trigger else trigger


def atr_n_position_size(*, capital: float, risk_fraction: float, atr: float | None, price: float | None,
                        lot_size: int = 1) -> int:
    """ATR-N sizing (ALG-047). Invalid stop distances or unfillable lots fail closed to zero units."""
    if atr is None or price is None or decision_inputs_ok([capital, price, atr]) != "OK":
        return 0
    if not 0 < risk_fraction <= 0.05 or lot_size < 1:
        return 0
    stop_distance = 2.0 * atr  # one N = one ATR; stop placed two N away
    raw = (capital * risk_fraction) / stop_distance
    units = int(raw // lot_size) * lot_size
    if units * price > capital:
        return 0
    return units


def trend_filtered_breakouts(closes: Sequence[float], highs: Sequence[float], ema_period: int = 50, n: int = 20) -> list[Signal]:
    """Negative-control challenger (ALG-049): breakout only when the close is above its lagged EMA.

    It is never presented as superior to momentum. Tests check that it is a strict subset of the basic breakouts.
    """
    if len(closes) < ema_period:
        return []
    alpha = 2 / (ema_period + 1)
    ema = closes[0]
    emas: list[float] = [ema]
    for c in closes[1:]:
        ema = alpha * c + (1 - alpha) * ema
        emas.append(ema)
    out: list[Signal] = []
    for s in basic_breakouts(closes, highs, n):
        if s.index >= 1 and closes[s.index - 1] > emas[s.index - 1]:
            out.append(Signal(s.index, s.side, s.reference_level, f"{s.rule}_trend_filter"))
    return out


def incremental_pnl(strategy: Sequence[float], baseline: Sequence[float]) -> dict[str, float]:
    """Paired after-cost difference against a baseline (ALG-053, ALG-073). Synthetic CI demonstrates arithmetic only."""
    if len(strategy) != len(baseline):
        raise ValueError("series must be the same length")
    diffs = [s - b for s, b in zip(strategy, baseline, strict=True)]
    total = sum(diffs)
    return {"incremental_total": total, "mean_diff": total / len(diffs) if diffs else 0.0, "bars": float(len(diffs))}
