"""The deep feature library: price action and smart-money concepts, candles, indicators, weekly, volume, relative.

Pre-registered in ``PRE-REGISTERED deep feature library and learned combinations``. ``compute`` takes one name's
adjusted daily OHLCV (columns d, o, h, l, c, v; oldest first) and returns one row per day with every feature as
known **at that day's close** - rolling windows end at the current bar, swing points are used only once confirmed,
and a fair value gap is "filled" only from the bar that fills it. ``fvg_events`` lists every gap with what happened
next, for the event study.

Definitions that are easy to get subtly wrong are spelled out where they are computed.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

FVG_LOOKBACK = 20          #: sessions an unfilled gap stays "open" for the count and distance features
EPS = 1e-12


def _rsi(c: pd.Series, n: int) -> pd.Series:
    d = c.diff()
    up, dn = d.clip(lower=0).rolling(n).mean(), (-d.clip(upper=0)).rolling(n).mean()
    return 100 - 100 / (1 + up / (dn + EPS))


def _slope(x: pd.Series, n: int) -> pd.Series:
    return x / x.shift(n) - 1


def compute(bars: pd.DataFrame, market: pd.Series | None = None) -> pd.DataFrame:
    """Every library feature for one name. ``market``: Nifty 500 close indexed like ``bars['d']`` (optional)."""
    b = bars.reset_index(drop=True)
    o, h, l, c, v = (b[k].astype(float) for k in ("o", "h", "l", "c", "v"))
    f = pd.DataFrame({"d": b["d"]})
    prev_c = c.shift(1)
    tr = pd.concat([h - l, (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()
    rng = (h - l).replace(0, np.nan)
    ret = c.pct_change()
    value = v * c

    # ---------------------------------------------------------------- price action / smart-money concepts
    fv = _fvg_features(o.to_numpy(), h.to_numpy(), l.to_numpy(), c.to_numpy(), atr.to_numpy())
    for k, arr in fv.items():
        f[k] = arr
    hh20, ll20 = h.shift(1).rolling(20).max(), l.shift(1).rolling(20).min()
    f["sweep_bear"] = ((h > hh20) & (c < hh20)).astype(float)          # took the highs, closed back inside
    f["sweep_bull"] = ((l < ll20) & (c > ll20)).astype(float)
    sw = _structure(o.to_numpy(), h.to_numpy(), l.to_numpy(), c.to_numpy(), atr.to_numpy())
    for k, arr in sw.items():
        f[k] = arr
    f["inside_bar"] = ((h < h.shift(1)) & (l > l.shift(1))).astype(float)
    f["outside_bar"] = ((h > h.shift(1)) & (l < l.shift(1))).astype(float)
    r = h - l
    f["nr4"] = (r <= r.rolling(4).min()).astype(float)
    f["nr7"] = (r <= r.rolling(7).min()).astype(float)
    f["wide_range"] = (r > 2 * atr).astype(float)
    f["gap_open"] = o / prev_c - 1
    f["gap_up_unfilled"] = ((o > h.shift(1)) & (l > h.shift(1))).astype(float)
    f["gap_down_unfilled"] = ((o < l.shift(1)) & (h < l.shift(1))).astype(float)
    f["close_location"] = (c - l) / rng
    f["upper_wick"] = (h - np.maximum(o, c)) / rng
    f["lower_wick"] = (np.minimum(o, c) - l) / rng
    f["body"] = (c - o).abs() / rng
    up = (c > prev_c).astype(int)
    f["up_streak"] = up.groupby((up != up.shift()).cumsum()).cumsum() * up
    dn = (c < prev_c).astype(int)
    f["down_streak"] = dn.groupby((dn != dn.shift()).cumsum()).cumsum() * dn

    # ---------------------------------------------------------------- candles
    body, bull, bear = (c - o).abs(), c > o, c < o
    lw, uw = np.minimum(o, c) - l, h - np.maximum(o, c)
    f["c_doji"] = (body <= 0.1 * rng).astype(float)
    f["c_hammer"] = ((lw >= 2 * body) & (uw <= 0.3 * body + EPS) & (c < c.shift(5))).astype(float)
    f["c_shooting_star"] = ((uw >= 2 * body) & (lw <= 0.3 * body + EPS) & (c > c.shift(5))).astype(float)
    po, pc = o.shift(1), c.shift(1)
    f["c_bull_engulf"] = (bull & (pc < po) & (c >= po) & (o <= pc)).astype(float)
    f["c_bear_engulf"] = (bear & (pc > po) & (c <= po) & (o >= pc)).astype(float)
    f["c_bull_harami"] = (bull & (pc < po) & (o > pc) & (c < po)).astype(float)
    f["c_piercing"] = (bull & (pc < po) & (o < l.shift(1)) & (c > (po + pc) / 2) & (c < po)).astype(float)
    f["c_dark_cloud"] = (bear & (pc > po) & (o > h.shift(1)) & (c < (po + pc) / 2) & (c > po)).astype(float)
    b2 = (c.shift(1) - o.shift(1)).abs()
    f["c_morning_star"] = ((c.shift(2) < o.shift(2)) & (b2 < 0.3 * (o.shift(2) - c.shift(2)).abs()) & bull
                           & (c > (o.shift(2) + c.shift(2)) / 2)).astype(float)
    f["c_evening_star"] = ((c.shift(2) > o.shift(2)) & (b2 < 0.3 * (c.shift(2) - o.shift(2)).abs()) & bear
                           & (c < (o.shift(2) + c.shift(2)) / 2)).astype(float)
    f["c_three_soldiers"] = (bull & (c.shift(1) > o.shift(1)) & (c.shift(2) > o.shift(2)) & (c > c.shift(1)) & (c.shift(1) > c.shift(2))).astype(float)
    f["c_three_crows"] = (bear & (c.shift(1) < o.shift(1)) & (c.shift(2) < o.shift(2)) & (c < c.shift(1)) & (c.shift(1) < c.shift(2))).astype(float)
    f["c_marubozu"] = (body >= 0.9 * rng).astype(float) * np.sign(c - o)

    # ---------------------------------------------------------------- indicators
    for n in (2, 7, 14, 28):
        f[f"rsi_{n}"] = _rsi(c, n)
    for n in (5, 14):
        lo, hi = l.rolling(n).min(), h.rolling(n).max()
        f[f"stoch_{n}"] = (c - lo) / (hi - lo + EPS)
    f["williams_r"] = (h.rolling(14).max() - c) / (h.rolling(14).max() - l.rolling(14).min() + EPS)
    tp = (h + l + c) / 3
    f["cci_20"] = (tp - tp.rolling(20).mean()) / (0.015 * tp.rolling(20).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True) + EPS)
    for n in (5, 10, 20, 60, 120, 250):
        f[f"roc_{n}"] = _slope(c, n)
    for n in (10, 20, 50, 100, 200):
        ma = c.rolling(n).mean()
        f[f"dist_ma_{n}"] = c / ma - 1
        f[f"ma_slope_{n}"] = _slope(ma, 10)
    f["cross_20_50"] = (c.rolling(20).mean() > c.rolling(50).mean()).astype(float)
    f["cross_50_200"] = (c.rolling(50).mean() > c.rolling(200).mean()).astype(float)
    m20, s20 = c.rolling(20).mean(), c.rolling(20).std()
    f["bb_pctb"] = (c - (m20 - 2 * s20)) / (4 * s20 + EPS)
    f["bb_width"] = 4 * s20 / (m20 + EPS)
    ema20 = c.ewm(span=20, adjust=False).mean()
    f["keltner_pos"] = (c - ema20) / (2 * atr + EPS)
    for n in (20, 55, 250):
        hi, lo = h.rolling(n).max(), l.rolling(n).min()
        f[f"donchian_{n}"] = (c - lo) / (hi - lo + EPS)
    f["atr_pct"] = atr / c
    f["atr_squeeze"] = tr.rolling(5).mean() / (tr.rolling(20).mean() + EPS)
    dmp = (h.diff()).where((h.diff() > -l.diff()) & (h.diff() > 0), 0.0)
    dmm = (-l.diff()).where((-l.diff() > h.diff()) & (-l.diff() > 0), 0.0)
    dip, dim = 100 * dmp.rolling(14).mean() / (atr + EPS), 100 * dmm.rolling(14).mean() / (atr + EPS)
    dx = 100 * (dip - dim).abs() / (dip + dim + EPS)
    f["adx_14"], f["di_spread"] = dx.rolling(14).mean(), dip - dim
    macd = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    f["macd_hist"] = (macd - macd.ewm(span=9, adjust=False).mean()) / c
    f["aroon_up"] = h.rolling(26).apply(lambda x: np.argmax(x) / 25.0, raw=True)
    f["aroon_down"] = l.rolling(26).apply(lambda x: np.argmin(x) / 25.0, raw=True)
    for n in (5, 20, 60, 250):
        f[f"vol_{n}"] = ret.rolling(n).std() * np.sqrt(252)
    f["vol_ratio_5_60"] = f["vol_5"] / (f["vol_60"] + EPS)
    f["vol_ratio_20_250"] = f["vol_20"] / (f["vol_250"] + EPS)
    f["skew_60"] = ret.rolling(60).skew()
    f["max_ret_20"] = ret.rolling(20).max()
    hi250 = h.rolling(250).max()
    f["dist_52w_high"] = c / hi250 - 1
    f["dist_52w_low"] = c / l.rolling(250).min() - 1
    f["days_since_high"] = h.rolling(250).apply(lambda x: len(x) - 1 - np.argmax(x), raw=True)
    f["mom_12_1"] = c.shift(21) / c.shift(252) - 1

    # ---------------------------------------------------------------- volume
    f["turnover_5_20"] = value.rolling(5).mean() / (value.rolling(20).mean() + EPS)
    f["turnover_20_250"] = value.rolling(20).mean() / (value.rolling(250).mean() + EPS)
    obv = (np.sign(c.diff()).fillna(0) * v).cumsum()
    f["obv_slope_20"] = (obv - obv.shift(20)) / (v.rolling(20).sum() + EPS)
    mfm = ((c - l) - (h - c)) / rng
    f["cmf_20"] = (mfm * v).rolling(20).sum() / (v.rolling(20).sum() + EPS)
    f["amihud_20"] = (ret.abs() / (value + EPS)).rolling(20).mean() * 1e9
    f["dist_vwap_20"] = c / ((tp * v).rolling(20).sum() / (v.rolling(20).sum() + EPS)) - 1
    f["adv20"] = value.rolling(20).mean()

    # ---------------------------------------------------------------- weekly timeframe (rolling 5-session bars)
    wk = c.shift(0)
    wchg = [wk.shift(5 * k) / wk.shift(5 * k + 5) - 1 for k in range(14)]
    W = pd.concat(wchg, axis=1)
    f["rsi_w14"] = 100 - 100 / (1 + W.clip(lower=0).mean(axis=1) / ((-W.clip(upper=0)).mean(axis=1) + EPS))
    f["dist_ma_10w"] = c / c.rolling(50).mean() - 1
    f["dist_ma_40w"] = c / c.rolling(200).mean() - 1
    wh = [h.shift(5 * k).rolling(5).max() for k in range(3)]
    wl = [l.shift(5 * k).rolling(5).min() for k in range(3)]
    f["fvg_w_bull_new"] = (wl[0] > wh[2]).astype(float)
    f["fvg_w_bear_new"] = (wh[0] < wl[2]).astype(float)

    # ---------------------------------------------------------------- relative
    if market is not None:
        mk = pd.Series(market).reindex(b["d"]).to_numpy()
        mk = pd.Series(mk, index=b.index)
        for n in (20, 60):
            f[f"rel_{n}"] = _slope(c, n) - _slope(mk, n)

    # ---------------------------------------------------------------- outcomes (not features)
    f["fwd5"] = c.shift(-5) / c - 1
    f["fwd20"] = c.shift(-20) / c - 1
    return f


OUTCOMES = ("fwd5", "fwd20")
META = ("d", "adv20")


def _fvg_features(o, h, l, c, atr) -> dict:
    """Bullish gap formed at t: l[t] > h[t-2] (bottom h[t-2], top l[t]). Bearish: h[t] < l[t-2] (bottom h[t],
    top l[t-2]). A bullish gap is *filled* once a later low reaches its bottom; *retested* on a bar whose low enters
    it (<= top) and whose close holds above its bottom. Only gaps formed before t are tested against bar t."""
    n = len(c)
    out = {k: np.full(n, np.nan) for k in ("fvg_bull_new", "fvg_bear_new", "fvg_bull_open", "fvg_bear_open",
                                            "fvg_bull_dist", "fvg_bear_dist", "fvg_bull_retest", "fvg_bear_retest")}
    bull, bear = [], []                                  # [formed_at, bottom, top]
    for t in range(n):
        a = atr[t] if np.isfinite(atr[t]) and atr[t] > 0 else np.nan
        # test existing gaps against this bar first: they were known before it
        rb = rs = 0.0
        keep = []
        for g in bull:
            if l[t] <= g[1]:
                continue                                 # fully filled: gone
            if l[t] <= g[2] and c[t] > g[1]:
                rb = 1.0
            keep.append(g)
        bull = [g for g in keep if t - g[0] <= FVG_LOOKBACK]
        keep = []
        for g in bear:
            if h[t] >= g[2]:
                continue
            if h[t] >= g[1] and c[t] < g[2]:
                rs = 1.0
            keep.append(g)
        bear = [g for g in keep if t - g[0] <= FVG_LOOKBACK]
        out["fvg_bull_retest"][t], out["fvg_bear_retest"][t] = rb, rs
        out["fvg_bull_new"][t] = out["fvg_bear_new"][t] = 0.0
        if t >= 2:
            if l[t] > h[t - 2]:
                bull.append([t, h[t - 2], l[t]])
                out["fvg_bull_new"][t] = (l[t] - h[t - 2]) / a if a == a else 1.0
            if h[t] < l[t - 2]:
                bear.append([t, h[t], l[t - 2]])
                out["fvg_bear_new"][t] = (l[t - 2] - h[t]) / a if a == a else 1.0
        out["fvg_bull_open"][t], out["fvg_bear_open"][t] = len(bull), len(bear)
        below = [g[2] for g in bull if g[2] <= c[t]]
        above = [g[1] for g in bear if g[1] >= c[t]]
        out["fvg_bull_dist"][t] = (c[t] - max(below)) / a if below and a == a else np.nan
        out["fvg_bear_dist"][t] = (min(above) - c[t]) / a if above and a == a else np.nan
    return out


def _structure(o, h, l, c, atr) -> dict:
    """Swing highs/lows are 5-bar fractals, usable only 2 bars after the pivot (when they are confirmed). Break of
    structure: today's close crosses the last confirmed swing. Order block: the low (high) of the last bearish
    (bullish) candle before an upward (downward) break, kept until a close through it invalidates it."""
    n = len(c)
    out = {k: np.full(n, np.nan) for k in ("bos_up", "bos_down", "ob_bull_dist", "ob_bear_dist", "hh_hl_count")}
    last_sh = last_sl = np.nan
    last_bear_low = last_bull_high = np.nan
    ob_bull = ob_bear = np.nan
    highs, lows = [], []
    for t in range(n):
        k = t - 2                                         # a pivot at k is confirmed at t = k + 2
        if k >= 2 and h[k] > max(h[k - 2], h[k - 1], h[k + 1], h[k + 2]):
            last_sh = h[k]
            highs.append(h[k])
        if k >= 2 and l[k] < min(l[k - 2], l[k - 1], l[k + 1], l[k + 2]):
            last_sl = l[k]
            lows.append(l[k])
        up = last_sh == last_sh and c[t] > last_sh and (t == 0 or c[t - 1] <= last_sh)
        dn = last_sl == last_sl and c[t] < last_sl and (t == 0 or c[t - 1] >= last_sl)
        out["bos_up"][t], out["bos_down"][t] = float(up), float(dn)
        if up and last_bear_low == last_bear_low:
            ob_bull = last_bear_low
        if dn and last_bull_high == last_bull_high:
            ob_bear = last_bull_high
        if ob_bull == ob_bull and c[t] < ob_bull:
            ob_bull = np.nan                              # closed through it: invalidated
        if ob_bear == ob_bear and c[t] > ob_bear:
            ob_bear = np.nan
        a = atr[t] if np.isfinite(atr[t]) and atr[t] > 0 else np.nan
        out["ob_bull_dist"][t] = (c[t] - ob_bull) / a if ob_bull == ob_bull and a == a else np.nan
        out["ob_bear_dist"][t] = (ob_bear - c[t]) / a if ob_bear == ob_bear and a == a else np.nan
        hh = sum(1 for i in range(1, min(len(highs), 4)) if highs[-i] > highs[-i - 1])
        hl = sum(1 for i in range(1, min(len(lows), 4)) if lows[-i] > lows[-i - 1])
        out["hh_hl_count"][t] = hh + hl
        if c[t] < o[t]:
            last_bear_low = l[t]
        elif c[t] > o[t]:
            last_bull_high = h[t]
    return out


def fvg_events(bars: pd.DataFrame) -> pd.DataFrame:
    """Every gap with what happened next: filled within 5 / 20 sessions, the first fill-and-hold retest day."""
    b = bars.reset_index(drop=True)
    h, l, c = (b[k].to_numpy(dtype=float) for k in ("h", "l", "c"))
    rows = []
    for t in range(2, len(c)):
        for side, ok, bottom, top in (("bull", l[t] > h[t - 2], h[t - 2], l[t]), ("bear", h[t] < l[t - 2], h[t], l[t - 2])):
            if not ok:
                continue
            filled, retest = None, None
            for j in range(t + 1, min(len(c), t + 21)):
                if side == "bull":
                    if retest is None and l[j] <= top and c[j] > bottom:
                        retest = j
                    if l[j] <= bottom:
                        filled = j
                        break
                else:
                    if retest is None and h[j] >= bottom and c[j] < top:
                        retest = j
                    if h[j] >= top:
                        filled = j
                        break
            rows.append({"d": b["d"].iloc[t], "side": side, "size": (top - bottom) / c[t],
                         "filled_5": filled is not None and filled - t <= 5, "filled_20": filled is not None,
                         "retest_d": b["d"].iloc[retest] if retest is not None else None})
    return pd.DataFrame(rows)


def feature_names(f: pd.DataFrame) -> list[str]:
    return [k for k in f.columns if k not in OUTCOMES and k not in META]
