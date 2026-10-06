"""Group 2 of the hundred algorithms: technical and position-management rules run as complete trading systems.

Each rule turns one name's daily bars into a position (1 long, 0 flat) decided at the close and held from the next
session; the delivery round trip is charged on every change. The portfolio holds every name the rule is long,
equally weighted, and is compared with the equal-weighted universe the rule chose from, day by day. Single-instrument
money-management schemes (scale-trader, grid, martingale, anti-martingale) and the VIX and circuit-band studies run
on the Nifty 50 and the cross-section respectively.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

ROUND_TRIP = {"mega": 0.0025, "large": 0.0030, "mid": 0.0045}


def _ema(x, n):
    return x.ewm(span=n, adjust=False).mean()


def _rsi(c, n=14):
    d = c.diff()
    up, dn = d.clip(lower=0).rolling(n).mean(), (-d.clip(upper=0)).rolling(n).mean()
    return 100 - 100 / (1 + up / (dn + 1e-12))


def _hold(enter: pd.Series, exit_: pd.Series) -> pd.Series:
    """A position that switches on at ``enter`` and off at ``exit_`` (enter wins a tie)."""
    pos, out = 0, np.zeros(len(enter))
    e, x = enter.fillna(False).to_numpy(), exit_.fillna(False).to_numpy()
    for i in range(len(e)):
        if e[i]:
            pos = 1
        elif x[i]:
            pos = 0
        out[i] = pos
    return pd.Series(out, index=enter.index)


def _hold_n(enter: pd.Series, n: int) -> pd.Series:
    out, left = np.zeros(len(enter)), 0
    for i, e in enumerate(enter.fillna(False).to_numpy()):
        left = n if e else max(left - 1, 0)
        out[i] = 1.0 if left > 0 else 0.0
    return pd.Series(out, index=enter.index)


def rules(b: pd.DataFrame) -> dict[str, pd.Series]:
    """Every rule's position for one name, as of each close. ``b``: o, h, l, c, v (oldest first)."""
    o, h, l, c, v = (b[k].astype(float) for k in ("o", "h", "l", "c", "v"))
    sma = lambda n: c.rolling(n).mean()                                   # noqa: E731
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()
    out = {}
    out["ma_cross_50_200"] = (sma(50) > sma(200)).astype(float).where(sma(200).notna(), 0)            # 56
    e = [_ema(c, n) for n in (8, 13, 21, 34, 55)]
    out["ema_ribbon"] = pd.concat([(e[i] > e[i + 1]) for i in range(4)], axis=1).all(axis=1).astype(float)  # 57
    macd = _ema(c, 12) - _ema(c, 26)
    out["macd_cross"] = (macd > _ema(macd, 9)).astype(float)                                          # 58
    sd = c.rolling(20).std()
    out["bollinger_reversion"] = _hold(c < sma(20) - 2 * sd, c > sma(20))                            # 59
    r = _rsi(c)
    out["rsi_exhaustion"] = _hold(r < 30, r > 50)                                                    # 60
    k = 100 * (c - l.rolling(14).min()) / (h.rolling(14).max() - l.rolling(14).min() + 1e-12)
    k = k.rolling(3).mean()
    dline = k.rolling(3).mean()
    out["stochastic_cross"] = _hold((k > dline) & (k.shift() <= dline.shift()) & (k < 20), k > 80)      # 61
    out["atr_breakout"] = _hold(c > c.shift() + 1.5 * atr.shift(), c < _ema(c, 20))                  # 62
    mid = lambda n: (h.rolling(n).max() + l.rolling(n).min()) / 2        # noqa: E731
    tenkan, kijun = mid(9), mid(26)
    top = np.maximum(((tenkan + kijun) / 2).shift(26), mid(52).shift(26))
    out["ichimoku_breakout"] = ((c > top) & (tenkan > kijun)).astype(float)                          # 63
    out["donchian_55_20"] = _hold(c > h.shift().rolling(55).max(), c < l.shift().rolling(20).min())  # 64
    tp = (h + l + c) / 3
    cci = (tp - tp.rolling(20).mean()) / (0.015 * (tp - tp.rolling(20).mean()).abs().rolling(20).mean() + 1e-12)
    out["cci_trend"] = _hold(cci > 100, cci < 0)                                                     # 65
    val = v * c
    out["volume_spike_entry"] = _hold_n((val > 3 * val.shift().rolling(20).mean()) & (c > c.shift()), 10)  # 83
    return out


def backtest(panel: dict[str, pd.DataFrame], universe: pd.DataFrame, *, costs: dict | None = None) -> pd.DataFrame:
    """``panel``: lineage -> bars with d, o, h, l, c, v and a liquidity ``bucket``. ``universe``: (d, lineage) rows that
    are eligible. Returns daily portfolio and universe returns per rule."""
    costs = costs or ROUND_TRIP
    elig = set(map(tuple, universe[["d", "lineage"]].to_numpy()))
    frames = []
    for lin, b in panel.items():
        b = b.reset_index(drop=True)
        ret = b["c"].pct_change().shift(-1)                               # held from this close to the next
        rt = costs.get(b["bucket"].iloc[0], costs["mid"])
        ok = pd.Series([(d, lin) in elig for d in b["d"]])
        for name, pos in rules(b).items():
            pos = pos.where(ok, 0.0)
            change = pos.diff().abs().fillna(pos)
            frames.append(pd.DataFrame({"d": b["d"], "rule": name, "lineage": lin, "pos": pos,
                                        "net": pos * ret - change * rt / 2, "ret": ret, "ok": ok,
                                        "active": (pos > 0) | (change > 0)}))
    f = pd.concat(frames, ignore_index=True).dropna(subset=["ret"])
    univ = f[f["ok"]].drop_duplicates(["d", "lineage"]).groupby("d")["ret"].mean().rename("universe")
    # an equal-weighted book: every name held or traded that day counts once, so entry and exit costs are shared
    held = f[f["active"]].groupby(["rule", "d"]).agg(port=("net", "mean"), names=("pos", "sum")).reset_index()
    grid = pd.MultiIndex.from_product([sorted(f["rule"].unique()), univ.index], names=["rule", "d"]).to_frame(index=False)
    out = grid.merge(held, on=["rule", "d"], how="left").fillna({"port": 0.0, "names": 0})   # flat, not missing
    out = out.merge(univ, left_on="d", right_index=True, how="left")
    return out


def summarise(daily: pd.DataFrame, split) -> pd.DataFrame:
    rows = []
    for rule, g in daily.groupby("rule"):
        g = g.sort_values("d").assign(excess=lambda x: x["port"] - x["universe"])
        g["month"] = pd.to_datetime(g["d"]).dt.to_period("M")
        for per, q in (("all", g), ("held-out", g[g["d"] >= split])):
            m = q.groupby("month")["excess"].sum()
            t = float(m.mean() / (m.std(ddof=1) / np.sqrt(len(m)))) if len(m) > 2 and m.std(ddof=1) > 0 else float("nan")
            rows.append({"rule": rule, "period": per, "excess_pa": float(m.mean() * 12), "t": t,
                         "time_in_market": float((q["names"] > 0).mean()), "avg_names": float(q["names"].mean())})
    return pd.DataFrame(rows)


# ------------------------------------------------------------------------- single-instrument money management

def scale_trader(c: np.ndarray, step: float = 0.02, unit: float = 0.1) -> np.ndarray:
    """#77: buy a unit each time the price falls `step` from the last trade, sell one each time it rises `step`.
    Returns the daily exposure (0..1)."""
    exp, last, out = 0.0, c[0], np.zeros(len(c))
    for i, p in enumerate(c):
        while p <= last * (1 - step) and exp < 1:
            exp, last = min(exp + unit, 1.0), last * (1 - step)
        while p >= last * (1 + step) and exp > 0:
            exp, last = max(exp - unit, 0.0), last * (1 + step)
        out[i] = exp
    return out


def grid(c: np.ndarray, width: float = 0.01, levels: int = 10, window: int = 20) -> np.ndarray:
    """#78: exposure set by how many grid steps the price is below its 20-day mean (more below = more long)."""
    m = pd.Series(c).rolling(window).mean().to_numpy()
    k = np.floor((m - c) / (m * width))
    return np.clip(np.nan_to_num(k) / levels + 0.5, 0, 1)


def martingale(r: np.ndarray, *, capital: float = 1.0, base: float = 0.01, anti: bool = False) -> dict:
    """#79 / #80: a daily long bet on the Nifty; martingale doubles the stake after a loss and resets after a win,
    anti-martingale doubles after a win and resets after a loss. Ruin when the stake cannot be covered."""
    eq, stake, peak, mdd, path = capital, base, capital, 0.0, []
    for x in r:
        if stake > eq:
            return {"ruined": True, "final": eq, "max_drawdown": 1.0, "days": len(path)}
        pnl = stake * x
        eq += pnl
        win = pnl > 0
        stake = (base if win else stake * 2) if not anti else (stake * 2 if win else base)
        stake = min(stake, eq) if anti else stake
        peak = max(peak, eq)
        mdd = max(mdd, 1 - eq / peak)
        path.append(eq)
    return {"ruined": False, "final": eq, "max_drawdown": mdd, "days": len(path)}
