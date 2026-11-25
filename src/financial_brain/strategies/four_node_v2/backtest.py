"""FourNode_India_v2 engine. Paper and research only.

A clone of Four-Node, preregistered in ``docs/strategies/four_node_v2/PREREGISTRATION.md``.
The v1 package is imported, never modified. Differences from v1: 15-minute bars, one fixed
interpretation (V0001000), a stop at the farther of N3 ± tick and 1 ATR, a minimum risk of 0.5% of
price, a 2 R target that stays active, an R-based trail, 1 ATR barrier clearance, and at most one
open position per stock.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from financial_brain.strategies.four_node.backtest import _atr, _sig, _smma
from financial_brain.strategies.four_node.indicators import FIB_RATIOS
from financial_brain.strategies.four_node.nodes import build
from financial_brain.strategies.four_node.spec import Variant, india_v1

NAME = "FourNode_India_v2"
CHOICES = {"BUY_EXCEPTION_SIDE": "LITERAL_BELOW", "BUY_N3_MID": "LITERAL_LE_MID",
           "N2_BUY_COMPARATOR": "LITERAL_LE_HIGH", "N4_DEFINITION": "BREAKOUT_CANDLE",
           "SIGNIFICANT_CANDLE": "BOTH_WICKS_GT_BODY", "FIB_ANCHOR": "PREV_DAY_LOW_TO_HIGH",
           "RETURN_CANDLE": "NO_OPPOSITE_CANDLE_SINCE_N3"}
PARAMS = {"stop_atr": 1.0, "max_risk_atr": 2.5, "min_risk_pct": 0.005, "target_r": 2.0,
          "trail_start_r": 1.0, "trail_step_r": 0.5, "min_struct_atr": 1.0, "max_d1_atr": 1.5,
          "max_d2_atr": 1.0, "n4_wick_atr": 0.5}
BARS = 25                 # 15-minute bars, 09:15 .. 15:15
LAST_ENTRY_BAR = 21       # 14:30
FLATTEN_BAR = 24          # 15:15
TICK = 0.05


def variant() -> Variant:
    v = Variant(NAME, "IN", True, params={**india_v1().params, **PARAMS})
    for k, alt in CHOICES.items():
        v.choose(k, alt, owner="owner-2026-11-18 (choices 1-3); preregistered v2 (4-7)")
    return v


def to_15m(o, h, lo, c):
    """(days, 75) 5-minute arrays -> (days, 25) 15-minute arrays."""
    d = o.shape[0]
    r = lambda a: a.reshape(d, 25, 3)  # noqa: E731
    return r(o)[:, :, 0], r(h).max(axis=2), r(lo).min(axis=2), r(c)[:, :, 2]


@dataclass(frozen=True)
class Trade:
    day: int
    bar: int
    side: str
    entry: float
    stop: float
    target: float
    exit: float
    reason: str
    net: float
    r: float


def _manage(side, entry, stop, target, risk, o, h, lo, c, start):
    long = side == "BUY"
    sign = 1 if long else -1
    p = PARAMS
    for t in range(start, BARS):
        if t > start:
            if (long and o[t] <= stop) or (not long and o[t] >= stop):
                return o[t], "stop", t
            if (long and o[t] >= target) or (not long and o[t] <= target):
                return o[t], "target", t
        if (long and lo[t] <= stop) or (not long and h[t] >= stop):
            return stop, "stop", t
        if (long and h[t] >= target) or (not long and lo[t] <= target):
            return target, "target", t
        if t >= FLATTEN_BAR:
            return c[t], "time", t
        gain_r = (c[t] - entry) * sign / risk
        if gain_r >= p["trail_start_r"]:
            locked = 0.5 * risk + p["trail_step_r"] * risk * int((gain_r - p["trail_start_r"]) // p["trail_step_r"])
            new = entry + sign * locked
            if (new - stop) * sign > 0:
                stop = new
    return c[BARS - 1], "time", BARS - 1


def trades_for(o5, h5, l5, c5, rt_cost: float) -> list[Trade]:
    o, h, lo, c = to_15m(o5, h5, l5, c5)
    p = PARAMS
    fo, fh, fl, fc = (a.reshape(-1) for a in (o, h, lo, c))
    s_lo, s_mid, s_hi = _smma(fl, 20), _smma(fc, 20), _smma(fh, 20)
    ma = [_smma(fc, n) for n in (50, 100, 200)]
    atr = _atr(fh, fl, fc)
    ratios = np.array(FIB_RATIOS) / 100
    out: list[Trade] = []
    for d in range(1, o.shape[0]):
        if np.isnan(c[d]).any() or np.isnan(c[d - 1]).any():
            continue
        pdl, pdh = float(lo[d - 1].min()), float(h[d - 1].max())
        fib = pdl + (pdh - pdl) * ratios
        z = build(list(h[d]), list(lo[d]))
        seen: set[tuple[int, int, int]] = set()
        busy_until = -1
        for t in range(4, LAST_ENTRY_BAR + 1):
            if t <= busy_until:
                continue
            tri = z.last_three(t)
            if tri is None:
                continue
            n1, n2, n3 = tri
            key = (n1.pivot_at, n2.pivot_at, n3.pivot_at)
            g = d * BARS + t
            unit = atr[g]
            if key in seen or np.isnan(unit) or unit <= 0 or np.isnan(s_mid[g]) or np.isnan(ma[2][g]):
                continue
            pat = (n1.side, n2.side, n3.side)
            close = float(c[d, t])
            if pat == (1, -1, 1) and close < n2.price:
                side = "SELL"
                if n3.price >= n1.price:
                    path_ok = True                                   # exception path
                    exc = True
                else:
                    path_ok = n1.price >= s_hi[g] and n3.price <= s_mid[g]
                    exc = False
                band_ok = n2.price <= s_lo[g]
            elif pat == (-1, 1, -1) and close > n2.price:
                side, exc = "BUY", False
                path_ok = n1.price <= s_lo[g] and n3.price <= s_mid[g]   # literal choices 2, 3
                band_ok = n2.price <= s_hi[g]
            elif pat == (-1, 1, -1) and close < n2.price and n3.price >= n1.price:
                side, exc, path_ok = "BUY", True, True                    # literal choice 1
                band_ok = n2.price <= s_hi[g]
            else:
                continue
            seen.add(key)
            if not (path_ok and band_ok):
                continue
            sgn = 1 if side == "SELL" else -1
            if exc and ((c[d] - o[d]) * sgn)[n3.pivot_at + 1:t + 1].max(initial=-1) > 0:
                continue                                                  # a return candle since N3
            if abs(n2.price - n3.price) / unit > p["max_d1_atr"] or abs(close - n2.price) / unit >= p["max_d2_atr"]:
                continue
            both, _, wick = _sig(o[d, t], h[d, t], lo[d, t], c[d, t])
            if both or wick >= p["n4_wick_atr"] * unit:
                continue                                                  # N4 = breakout candle
            if t + 1 >= BARS:
                continue
            entry = float(o[d, t + 1])
            structural = n3.price + TICK if side == "SELL" else n3.price - TICK
            atr_stop = entry + p["stop_atr"] * unit if side == "SELL" else entry - p["stop_atr"] * unit
            stop = max(structural, atr_stop) if side == "SELL" else min(structural, atr_stop)
            risk = abs(entry - stop)
            if risk <= 0 or risk / unit > p["max_risk_atr"] or risk / entry < p["min_risk_pct"]:
                continue
            ahead = [b for b in list(fib) + [m[g] for m in ma] if (b > entry if side == "BUY" else b < entry)]
            if any(abs(entry - b) < p["min_struct_atr"] * unit for b in ahead):
                continue
            target = entry - p["target_r"] * risk if side == "SELL" else entry + p["target_r"] * risk
            px, why, end = _manage(side, entry, stop, target, risk, o[d], h[d], lo[d], c[d], t + 1)
            s = 1 if side == "BUY" else -1
            out.append(Trade(d, t, side, entry, stop, target, px, why,
                             (px - entry) / entry * s - rt_cost, (px - entry) * s / risk))
            busy_until = end
    return out
