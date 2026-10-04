"""Opening-range breakout, with a stop and a target, simulated minute by minute.

Pre-registered in ``PRE-REGISTERED swing and intraday setups with stops``.

    opening range   high and low of 09:15-09:29
    trigger         from 09:30 to 11:00, the first minute that CLOSES beyond the range
    entry           the NEXT minute's open (the trigger minute is already over)
    stop            the opposite end of the range ('full') or its midpoint ('mid')
    target          entry +/- 2R, R = distance from entry to the stop
    time exit       the 15:15 close

Fill rules are chosen to be hard on the strategy, not generous: a minute that opens beyond the stop fills
at the open, not the stop; a minute whose range touches both the stop and the target is counted as a stop;
and a trade whose entry is already at or beyond its own stop is skipped, not counted.

Costs are the intraday schedule (STT on the sell leg only, capped brokerage, exchange, GST) plus impact by
liquidity bucket, which is why a trade that wins half the time and earns its full R can still lose money.
"""
from __future__ import annotations

import numpy as np

M_OPEN, M_OR_END, M_TRIGGER_END, M_EXIT = 555, 570, 660, 915
TARGET_R = 2.0


class OrbError(ValueError):
    pass


def simulate_day(m, o, h, l, c, *, side: str, stop_mode: str = "full", target_r: float = TARGET_R) -> dict | None:
    """One session's trade for one side, or None if there is no trade. Arrays are minute bars in order."""
    if side not in ("long", "short"):
        raise OrbError("side must be long or short")
    if stop_mode not in ("full", "mid"):
        raise OrbError("stop_mode must be full or mid")
    in_or = m < M_OR_END
    if in_or.sum() < 10:
        return None
    or_hi, or_lo = h[in_or].max(), l[in_or].min()
    if or_hi <= or_lo:
        return None
    window = (m >= M_OR_END) & (m <= M_TRIGGER_END)
    trig = window & ((c > or_hi) if side == "long" else (c < or_lo))
    idx = np.flatnonzero(trig)
    if len(idx) == 0 or idx[0] + 1 >= len(m):
        return None
    k = idx[0] + 1
    entry = o[k]
    stop = (or_lo if side == "long" else or_hi) if stop_mode == "full" else (or_hi + or_lo) / 2.0
    risk = (entry - stop) if side == "long" else (stop - entry)
    if risk <= 0:
        return None
    target = entry + target_r * risk if side == "long" else entry - target_r * risk
    exit_px, why = None, "time"
    for j in range(k, len(m)):
        if m[j] > M_EXIT:
            break
        if side == "long":
            if o[j] <= stop or l[j] <= stop:
                exit_px, why = min(o[j], stop), "stop"
                break
            if h[j] >= target:
                exit_px, why = max(o[j], target) if o[j] >= target else target, "target"
                break
        else:
            if o[j] >= stop or h[j] >= stop:
                exit_px, why = max(o[j], stop), "stop"
                break
            if l[j] <= target:
                exit_px, why = min(o[j], target) if o[j] <= target else target, "target"
                break
        exit_px = c[j]
    if exit_px is None:
        return None
    pnl = (exit_px - entry) if side == "long" else (entry - exit_px)
    return {"entry": float(entry), "exit": float(exit_px), "ret": float(pnl / entry), "r": float(pnl / risk),
            "why": why, "risk_pct": float(risk / entry), "trigger_minute": int(m[k - 1])}
