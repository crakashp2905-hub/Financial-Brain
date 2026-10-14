"""``PRE-REGISTERED index mean reversion and VWAP``: nine Nifty rules from the owner's sources (QuantPedia, Tradezella).

Daily rules work on the 15:15 price (one-minute bars) and hold from one 15:15 to the next; each returns a position
series in {-1, 0, +1} decided on information up to that 15:15. ``timing_outcome`` is the registered statistic:
(w_t - its own trailing-250 mean) x (next return - cash) - 2 bp per unit of position changed. ``vwap_bounce`` is the
intraday rule V1. ``shift_null`` is G4: the same position series circularly shifted against the returns.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import standard as S

ONE_WAY = 0.0002
INTRADAY_RT = 0.0004
RF = 0.065
EXPOSURE_WINDOW = 250


def at_1515(minutes: pd.DataFrame) -> pd.Series:
    """The 15:15 price of each session (the last bar at or before 15:15)."""
    m = minutes.copy()
    m["ts"] = pd.to_datetime(m["ts"])
    m["d"] = m["ts"].dt.tz_localize(None).dt.normalize()
    m = m[m["ts"].dt.strftime("%H:%M") <= "15:15"]
    return m.groupby("d")["close"].last()


def rsi(p: pd.Series, n: int = 14) -> pd.Series:
    d = p.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    dn = (-d).clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    return 100 - 100 / (1 + up / dn)


def _hold(enter: pd.Series, exit_: pd.Series, side: int) -> pd.Series:
    """Enter on ``enter``, stay until ``exit_`` (checked from the next session on)."""
    w, cur = [], 0
    for e, x in zip(enter.fillna(False).to_numpy(), exit_.fillna(False).to_numpy()):
        if cur and x:
            cur = 0
        if not cur and e:
            cur = side
        w.append(cur)
    return pd.Series(w, index=enter.index, dtype=float)


def positions(p: pd.Series) -> dict[str, pd.Series]:
    r = p.pct_change()
    lo25 = r.rolling(250, min_periods=250).apply(lambda x: np.sort(x)[24], raw=True)
    hi25 = r.rolling(250, min_periods=250).apply(lambda x: np.sort(x)[-25], raw=True)
    rs = rsi(p)
    ma = p.rolling(10, min_periods=10).mean()
    hi10 = p.rolling(10, min_periods=10).max()
    lo10 = p.rolling(10, min_periods=10).min()
    above = p > ma
    out = {
        "q1_extreme_down_long": (r <= lo25).astype(float),
        "q2_extreme_up_short": -(r >= hi25).astype(float),
        "q3_rsi_long": _hold(rs < 35, rs > 55, +1),
        "q4_rsi_short": _hold(rs > 65, rs < 55, -1),
        "q5_above_ma10": above.astype(float).where(ma.notna(), 0.0),
        "q6_dip_ma10": _hold(p < ma, p >= hi10, +1),
    }
    cross_up = above & ~above.shift(1, fill_value=True)
    q7 = pd.Series(0.0, index=p.index)
    for i in np.flatnonzero(cross_up.to_numpy()):
        q7.iloc[i:i + 3] = 1.0
    out["q7_cross_ma10_3d"] = q7
    w, cur = [], 0
    for lo, hi in zip((p <= lo10).to_numpy(), (p >= hi10).to_numpy()):
        if lo:
            cur = 1
        elif hi:
            cur = -1
        w.append(cur)
    out["q8_ten_day_extremes"] = pd.Series(w, index=p.index, dtype=float).where(lo10.notna(), 0.0)
    return {k: v.fillna(0.0) for k, v in out.items()}


def timing_outcome(p: pd.Series, w: pd.Series) -> pd.Series:
    """Daily: (w_t - trailing mean of w) x (r_{t+1} - cash) - cost of changing w at t."""
    nxt = p.shift(-1) / p - 1
    days = pd.Series(p.index, index=p.index).diff().shift(-1).dt.days.fillna(1)
    excess = nxt - RF * days / 365
    wbar = w.shift(1).rolling(EXPOSURE_WINDOW, min_periods=EXPOSURE_WINDOW).mean()
    cost = w.diff().abs().fillna(w.abs()) * ONE_WAY
    return ((w - wbar) * excess - cost).dropna()


def monthly(daily: pd.Series) -> pd.Series:
    s = daily.groupby(daily.index.to_period("M")).sum()
    s.index = s.index.to_timestamp()
    return s


def shift_null(p: pd.Series, w: pd.Series, *, draws: int = S.NULL_DRAWS, seed: int = 20261014) -> dict:
    stat = S.replication(monthly(timing_outcome(p, w)))["stat"]
    rng = np.random.default_rng(seed)
    n = len(w)
    sims = []
    for _ in range(draws):
        k = int(rng.integers(EXPOSURE_WINDOW, n - EXPOSURE_WINDOW))
        ws = pd.Series(np.roll(w.to_numpy(), k), index=w.index)
        sims.append(S.replication(monthly(timing_outcome(p, ws)))["stat"])
    sims = np.array([x for x in sims if np.isfinite(x)])
    q = float(np.quantile(sims, S.NULL_QUANTILE))
    return {"stat": stat, "null_q95": q, "null_mean": float(sims.mean()), "draws": len(sims),
            "pass": bool(np.isfinite(stat) and stat > q)}


def vwap_bounce(minutes: pd.DataFrame) -> pd.Series:
    """V1: per session, the one trade's return (entry at the touching bar's close, exit at 15:15) minus 4 bp."""
    m = minutes.copy()
    m["ts"] = pd.to_datetime(m["ts"])
    m["d"] = m["ts"].dt.tz_localize(None).dt.normalize()
    m["hm"] = m["ts"].dt.strftime("%H:%M")
    out = {}
    for d, g in m.groupby("d"):
        g = g[g["hm"] <= "15:15"]
        if len(g) < 300:
            continue
        vol = g["volume"].where(g["volume"] > 0, 1.0)
        tp = (g["high"] + g["low"] + g["close"]) / 3
        vwap = (tp * vol).cumsum() / vol.cumsum()
        open_ = g["open"].iloc[0]
        exit_px = g["close"].iloc[-1]
        win = (g["hm"] >= "09:45") & (g["hm"] <= "11:30")
        prev_close = g["close"].shift(1)
        long_ok = win & (prev_close > vwap.shift(1)) & (g["close"] > open_) & (g["low"] <= vwap) & (g["close"] > vwap)
        short_ok = win & (prev_close < vwap.shift(1)) & (g["close"] < open_) & (g["high"] >= vwap) & (g["close"] < vwap)
        first = None
        for i in np.flatnonzero((long_ok | short_ok).to_numpy()):
            first = (i, +1 if long_ok.iloc[i] else -1)
            break
        if first is None:
            continue
        i, side = first
        entry = g["close"].iloc[i]
        out[d] = side * (exit_px / entry - 1) - INTRADAY_RT
    return pd.Series(out, dtype=float)
