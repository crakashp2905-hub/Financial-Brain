"""Four-Node v3 family engine (preregistered in docs/strategies/four_node_v3/PREREGISTRATION.md).

Each strategy is FourNode_India_v2 plus one configured change. v1 and v2 are imported, never
modified. ``CONTROL`` is the empty config and reproduces v2's setups.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from financial_brain.strategies.four_node.backtest import _atr, _sig, _smma
from financial_brain.strategies.four_node.indicators import FIB_RATIOS
from financial_brain.strategies.four_node.nodes import build
from financial_brain.strategies.four_node_v2.backtest import BARS, FLATTEN_BAR, LAST_ENTRY_BAR, PARAMS, TICK, \
    to_15m

STRATEGIES: dict[str, dict] = {
    "CONTROL_v2": {},
    "S01_hold_1d": {"hold_days": 1},
    "S02_hold_3d": {"hold_days": 3},
    "S03_trend_15m": {"trend_15m": True},
    "S04_trend_daily": {"trend_daily": True},
    "S05_volume": {"vol_mult": 1.5},
    "S06_morning": {"window": (2, 8)},            # breakout bar 09:45 .. 11:15 start (ends 11:30)
    "S07_t15_be": {"target_r": 1.5, "breakeven_only": True},
    "S08_vol_regime": {"atr_regime": True},
    "S09_orb_align": {"orb": True},
    "S10_combo": {"trend_15m": True, "vol_mult": 1.5, "window": (2, 8)},
}


@dataclass(frozen=True)
class Trade:
    day: int
    side: str
    reason: str
    net: float
    r: float
    delivery: bool


def _manage(side, entry, stop, target, risk, fo, fh, fl, fc, start, end, cfg):
    """Manage from flat index ``start`` (entry at its open) to ``end`` (exit at its close)."""
    long = side == "BUY"
    sign = 1 if long else -1
    be_only = cfg.get("breakeven_only", False)
    for g in range(start, end + 1):
        if g > start:
            if (long and fo[g] <= stop) or (not long and fo[g] >= stop):
                return fo[g], "stop"
            if (long and fo[g] >= target) or (not long and fo[g] <= target):
                return fo[g], "target"
        if (long and fl[g] <= stop) or (not long and fh[g] >= stop):
            return stop, "stop"
        if (long and fh[g] >= target) or (not long and fl[g] <= target):
            return target, "target"
        if g == end:
            return fc[g], "time"
        gain_r = (fc[g] - entry) * sign / risk
        if be_only:
            if gain_r >= 1.0 and (entry - stop) * sign > 0:
                stop = entry
        elif gain_r >= PARAMS["trail_start_r"]:
            locked = 0.5 * risk + PARAMS["trail_step_r"] * risk * int(
                (gain_r - PARAMS["trail_start_r"]) // PARAMS["trail_step_r"])
            new = entry + sign * locked
            if (new - stop) * sign > 0:
                stop = new
    return fc[end], "time"


def trades_for(o5, h5, l5, c5, v5, cfg: dict, rt_intraday: float, rt_delivery: float) -> list[Trade]:
    o, h, lo, c = to_15m(o5, h5, l5, c5)
    nd = o.shape[0]
    vol = v5.reshape(nd, 25, 3).sum(axis=2)
    p = PARAMS
    target_r = cfg.get("target_r", p["target_r"])
    hold = cfg.get("hold_days", 0)
    w0, w1 = cfg.get("window", (4, LAST_ENTRY_BAR))
    fo, fh, fl, fc, fv = (a.reshape(-1) for a in (o, h, lo, c, vol))
    s_lo, s_mid, s_hi = _smma(fl, 20), _smma(fc, 20), _smma(fh, 20)
    ma = [_smma(fc, n) for n in (50, 100, 200)]
    atr = _atr(fh, fl, fc)
    daily_close = c[:, -1]
    d_smma = _smma(daily_close, 20)
    vavg = np.convolve(fv, np.ones(20) / 20, mode="full")[:len(fv)]
    atr_pct = atr / fc
    # trailing 60-session median of ATR%, sampled at each session's last bar, point-in-time
    sess_atr = atr_pct.reshape(nd, BARS)[:, -1]
    ratios = np.array(FIB_RATIOS) / 100
    out: list[Trade] = []
    busy_until = -1
    for d in range(1, nd):
        if np.isnan(c[d]).any() or np.isnan(c[d - 1]).any():
            continue
        pdl, pdh = float(lo[d - 1].min()), float(h[d - 1].max())
        fib = pdl + (pdh - pdl) * ratios
        z = build(list(h[d]), list(lo[d]))
        seen: set[tuple[int, int, int]] = set()
        regime_med = float(np.nanmedian(sess_atr[max(0, d - 60):d])) if d >= 20 else np.nan
        for t in range(max(4, w0), min(LAST_ENTRY_BAR, w1) + 1):
            g = d * BARS + t
            if g <= busy_until:
                continue
            tri = z.last_three(t)
            if tri is None:
                continue
            n1, n2, n3 = tri
            key = (n1.pivot_at, n2.pivot_at, n3.pivot_at)
            unit = atr[g]
            if key in seen or np.isnan(unit) or unit <= 0 or np.isnan(s_mid[g]) or np.isnan(ma[2][g]):
                continue
            pat = (n1.side, n2.side, n3.side)
            close = float(c[d, t])
            if pat == (1, -1, 1) and close < n2.price:
                side = "SELL"
                exc = n3.price >= n1.price
                path_ok = True if exc else (n1.price >= s_hi[g] and n3.price <= s_mid[g])
                band_ok = n2.price <= s_lo[g]
            elif pat == (-1, 1, -1) and close > n2.price:
                side, exc = "BUY", False
                path_ok = n1.price <= s_lo[g] and n3.price <= s_mid[g]
                band_ok = n2.price <= s_hi[g]
            elif pat == (-1, 1, -1) and close < n2.price and n3.price >= n1.price:
                side, exc, path_ok = "BUY", True, True
                band_ok = n2.price <= s_hi[g]
            else:
                continue
            seen.add(key)
            if not (path_ok and band_ok):
                continue
            sgn = 1 if side == "SELL" else -1
            if exc and ((c[d] - o[d]) * sgn)[n3.pivot_at + 1:t + 1].max(initial=-1) > 0:
                continue
            if abs(n2.price - n3.price) / unit > p["max_d1_atr"] or abs(close - n2.price) / unit >= p["max_d2_atr"]:
                continue
            both, _, wick = _sig(o[d, t], h[d, t], lo[d, t], c[d, t])
            if both or wick >= p["n4_wick_atr"] * unit:
                continue
            long = side == "BUY"
            # --- v3 filters -------------------------------------------------------------------
            if cfg.get("trend_15m") and (close <= ma[2][g] if long else close >= ma[2][g]):
                continue
            if cfg.get("trend_daily"):
                if np.isnan(d_smma[d - 1]) or (daily_close[d - 1] <= d_smma[d - 1] if long
                                               else daily_close[d - 1] >= d_smma[d - 1]):
                    continue
            if cfg.get("vol_mult") and (g < 20 or fv[g] < cfg["vol_mult"] * vavg[g - 1]):
                continue
            if cfg.get("atr_regime") and (np.isnan(regime_med) or atr_pct[g] <= regime_med):
                continue
            if cfg.get("orb"):
                if t < 2:
                    continue
                orh, orl = float(h[d, :2].max()), float(lo[d, :2].min())
                if (close <= orh) if long else (close >= orl):
                    continue
            # ----------------------------------------------------------------------------------
            if t + 1 >= BARS:
                continue
            entry = float(o[d, t + 1])
            structural = n3.price + TICK if side == "SELL" else n3.price - TICK
            atr_stop = entry + p["stop_atr"] * unit if side == "SELL" else entry - p["stop_atr"] * unit
            stop = max(structural, atr_stop) if side == "SELL" else min(structural, atr_stop)
            risk = abs(entry - stop)
            if risk <= 0 or risk / unit > p["max_risk_atr"] or risk / entry < p["min_risk_pct"]:
                continue
            ahead = [b for b in list(fib) + [m[g] for m in ma] if (b > entry if long else b < entry)]
            if any(abs(entry - b) < p["min_struct_atr"] * unit for b in ahead):
                continue
            target = entry + target_r * risk if long else entry - target_r * risk
            last_day = min(d + hold, nd - 1)
            if hold and np.isnan(c[d:last_day + 1]).any():
                continue
            end = last_day * BARS + (FLATTEN_BAR if hold == 0 else BARS - 1)
            px, why = _manage(side, entry, stop, target, risk, fo, fh, fl, fc, g + 1, end, cfg)
            s = 1 if long else -1
            cost = rt_delivery if hold else rt_intraday
            out.append(Trade(d, side, why, (px - entry) / entry * s - cost, (px - entry) * s / risk, bool(hold)))
            busy_until = end
    return out
