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

    # ---------------------------------------------------------------- the second family set
    for k, arr in extra(o, h, l, c, v, atr).items():
        f[k] = arr

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


FIB_LEVELS = (0.236, 0.382, 0.5, 0.618, 0.786)


def extra(o: pd.Series, h: pd.Series, l: pd.Series, c: pd.Series, v: pd.Series, atr: pd.Series) -> dict:
    """The second family set (added at the owner's request, before any result): Fibonacci, pivots, Ichimoku, SAR,
    Supertrend, Heikin-Ashi, the named oscillators, statistical trend measures, volume profile, anchored VWAP, round
    numbers, Gann square of nine, a Wyckoff-style compression and harmonic (Gartley) patterns. All as of the close."""
    out = {}
    a = atr.replace(0, np.nan)
    # ---- Fibonacci: the swing is the n-session high and low; which came last says whether we retrace an up-move
    for n in (60, 250):
        hi, lo = h.rolling(n).max(), l.rolling(n).min()
        rng = (hi - lo).replace(0, np.nan)
        pos = (c - lo) / rng
        t_hi = h.rolling(n).apply(np.argmax, raw=True)
        t_lo = l.rolling(n).apply(np.argmin, raw=True)
        upswing = t_hi > t_lo                              # the high is more recent: price retraces down from it
        retr = pd.Series(np.where(upswing, 1 - pos, pos), index=c.index)
        out[f"fib_retrace_{n}"] = retr
        out[f"fib_near_{n}"] = pd.concat([(retr - x).abs() for x in FIB_LEVELS], axis=1).min(axis=1) * rng / a
        out[f"fib_golden_pocket_{n}"] = ((retr >= 0.618) & (retr <= 0.65)).astype(float) * np.where(upswing, 1.0, -1.0)
        ext = np.where(upswing, lo + 1.618 * (hi - lo), hi - 1.618 * (hi - lo))
        out[f"fib_ext1618_dist_{n}"] = (pd.Series(ext, index=c.index) - c).abs() / a
    for n in (21, 55, 89, 144):
        out[f"dist_fib_ma_{n}"] = c / c.rolling(n).mean() - 1
    # ---- pivots from the previous session and the previous 5 sessions
    ph, pl, pc = h.shift(1), l.shift(1), c.shift(1)
    P = (ph + pl + pc) / 3
    out["pivot_dist"] = (c - P) / a
    out["pivot_r1_dist"] = (2 * P - pl - c) / a
    out["pivot_s1_dist"] = (c - (2 * P - ph)) / a
    out["camarilla_pos"] = (c - (pc - 1.1 * (ph - pl) / 4)) / (2 * 1.1 * (ph - pl) / 4 + EPS)
    wh, wl = h.shift(1).rolling(5).max(), l.shift(1).rolling(5).min()
    out["pivot_w_dist"] = (c - (wh + wl + pc) / 3) / a
    # ---- Ichimoku, with the cloud as it was published for today (computed 26 sessions ago)
    def mid(n):
        return (h.rolling(n).max() + l.rolling(n).min()) / 2
    tenkan, kijun = mid(9), mid(26)
    span_a, span_b = ((tenkan + kijun) / 2).shift(26), mid(52).shift(26)
    top, bot = np.maximum(span_a, span_b), np.minimum(span_a, span_b)
    out["ichi_tenkan_dist"] = (c - tenkan) / a
    out["ichi_kijun_dist"] = (c - kijun) / a
    out["ichi_tk_cross"] = np.sign(tenkan - kijun)
    out["ichi_cloud_pos"] = pd.Series(np.where(c > top, 1.0, np.where(c < bot, -1.0, 0.0)), index=c.index).where(top.notna())
    out["ichi_cloud_thick"] = (top - bot) / a
    out["ichi_chikou"] = c / c.shift(26) - 1
    # ---- loops: Parabolic SAR, Supertrend, Heikin-Ashi
    H, L, C, O = h.to_numpy(), l.to_numpy(), c.to_numpy(), o.to_numpy()
    A = atr.to_numpy()
    n_ = len(C)
    sar_dir, sar_dist = np.full(n_, np.nan), np.full(n_, np.nan)
    if n_ > 2:
        up, sar, ep, af = True, L[0], H[0], 0.02
        for t in range(1, n_):
            sar = sar + af * (ep - sar)
            if up:
                sar = min(sar, L[t - 1], L[t - 2] if t > 1 else L[t - 1])
                if L[t] < sar:
                    up, sar, ep, af = False, ep, L[t], 0.02
                elif H[t] > ep:
                    ep, af = H[t], min(af + 0.02, 0.2)
            else:
                sar = max(sar, H[t - 1], H[t - 2] if t > 1 else H[t - 1])
                if H[t] > sar:
                    up, sar, ep, af = True, ep, H[t], 0.02
                elif L[t] < ep:
                    ep, af = L[t], min(af + 0.02, 0.2)
            sar_dir[t] = 1.0 if up else -1.0
            sar_dist[t] = (C[t] - sar) / A[t] if A[t] == A[t] and A[t] > 0 else np.nan
    out["psar_dir"], out["psar_dist"] = sar_dir, sar_dist
    st_dir = np.full(n_, np.nan)
    hl2 = (H + L) / 2
    fu = fl = np.nan
    d = 1.0
    for t in range(1, n_):
        if not (A[t] == A[t]):
            continue
        bu, bl = hl2[t] + 3 * A[t], hl2[t] - 3 * A[t]
        fu = bu if (fu != fu or bu < fu or C[t - 1] > fu) else fu
        fl = bl if (fl != fl or bl > fl or C[t - 1] < fl) else fl
        d = 1.0 if C[t] > fu else -1.0 if C[t] < fl else d
        st_dir[t] = d
    out["supertrend_dir"] = st_dir
    ha_c = (O + H + L + C) / 4
    ha_o = np.full(n_, np.nan)
    if n_:
        ha_o[0] = (O[0] + C[0]) / 2
        for t in range(1, n_):
            ha_o[t] = (ha_o[t - 1] + ha_c[t - 1]) / 2
    col = pd.Series(np.sign(ha_c - ha_o), index=c.index)
    out["ha_color"] = col
    out["ha_streak"] = (col.groupby((col != col.shift()).cumsum()).cumcount() + 1) * col
    # ---- named oscillators
    def ema(x, n):
        return x.ewm(span=n, adjust=False).mean()
    e13 = ema(c, 13)
    out["elder_bull"], out["elder_bear"] = (h - e13) / a, (l - e13) / a
    out["trix_15"] = ema(ema(ema(np.log(c), 15), 15), 15).diff() * 1e4
    def roc(n):
        return c / c.shift(n) - 1
    out["kst"] = (roc(10).rolling(10).mean() + 2 * roc(15).rolling(10).mean() + 3 * roc(20).rolling(10).mean()
                  + 4 * roc(30).rolling(15).mean())
    prev = c.shift(1)
    bp = c - np.minimum(l, prev)
    trr = np.maximum(h, prev) - np.minimum(l, prev)
    def avg(n):
        return bp.rolling(n).sum() / (trr.rolling(n).sum() + EPS)
    out["ultimate_osc"] = 100 * (4 * avg(7) + 2 * avg(14) + avg(28)) / 7
    dch = c.diff()
    out["chande_mo_20"] = 100 * (dch.clip(lower=0).rolling(20).sum() - (-dch.clip(upper=0)).rolling(20).sum()) / (dch.abs().rolling(20).sum() + EPS)
    vmp, vmm = (h - l.shift(1)).abs().rolling(14).sum(), (l - h.shift(1)).abs().rolling(14).sum()
    out["vortex_spread"] = (vmp - vmm) / (trr.rolling(14).sum() + EPS)
    hl = h - l
    e9 = ema(hl, 9)
    out["mass_index"] = (e9 / (ema(e9, 9) + EPS)).rolling(25).sum()
    out["ease_of_move"] = (((h + l) / 2).diff() / ((v / 1e6) / (hl + EPS) + EPS)).rolling(14).mean() / c
    out["force_index_13"] = ema(c.diff() * v, 13) / (c * v.rolling(20).mean() + EPS)
    streak = np.sign(c.diff()).fillna(0)
    run = streak.groupby((streak != streak.shift()).cumsum()).cumsum()
    out["connors_rsi"] = (_rsi(c, 3) + _rsi(run, 2) + c.pct_change().rolling(100).rank(pct=True) * 100) / 3
    out["coppock"] = ema(roc(14) + roc(11), 10)
    out["dpo_20"] = (c - c.rolling(20).mean().shift(11)) / c
    macd = ema(c, 23) - ema(c, 50)
    k1 = ema((macd - macd.rolling(10).min()) / (macd.rolling(10).max() - macd.rolling(10).min() + EPS), 3)
    out["schaff_trend"] = ema((k1 - k1.rolling(10).min()) / (k1.rolling(10).max() - k1.rolling(10).min() + EPS), 3)
    # ---- statistical trend measures
    x = np.arange(20) - 9.5
    lc = np.log(c)
    out["linreg_slope_20"] = lc.rolling(20).apply(lambda y: (x * (y - y.mean())).sum() / (x * x).sum(), raw=True) * 20
    out["linreg_r2_20"] = lc.rolling(20).apply(lambda y: np.corrcoef(x, y)[0, 1] ** 2 if y.std() > 0 else np.nan, raw=True)
    out["zscore_20"] = (c - c.rolling(20).mean()) / (c.rolling(20).std() + EPS)
    out["efficiency_10"] = (c - c.shift(10)).abs() / (c.diff().abs().rolling(10).sum() + EPS)
    out["choppiness_14"] = 100 * np.log10(trr.rolling(14).sum() / (h.rolling(14).max() - l.rolling(14).min() + EPS)) / np.log10(14)
    r1 = lc.diff()
    out["variance_ratio_10"] = lc.diff(10).rolling(120).var() / (10 * r1.rolling(120).var() + EPS)
    # ---- levels: volume-profile point of control (60 sessions), anchored VWAP, round numbers, Gann square of nine
    tp = (H + L + C) / 3
    V = v.to_numpy(dtype=float)
    poc = np.full(n_, np.nan)
    for t in range(59, n_):
        seg, vol = tp[t - 59:t + 1], V[t - 59:t + 1]
        if not np.isfinite(seg).all() or vol.sum() <= 0 or not (A[t] == A[t] and A[t] > 0):
            continue
        edges = np.linspace(seg.min(), seg.max() + 1e-9, 31)
        idx = np.clip(np.searchsorted(edges, seg, side="right") - 1, 0, 29)
        b = int(np.argmax(np.bincount(idx, weights=vol, minlength=30)))
        poc[t] = (C[t] - (edges[b] + edges[b + 1]) / 2) / A[t]
    out["poc_dist_60"] = poc
    csum_pv, csum_v = np.cumsum(np.nan_to_num(tp * V)), np.cumsum(np.nan_to_num(V))
    for name, anchor in (("low", l.rolling(250).apply(np.argmin, raw=True)), ("high", h.rolling(250).apply(np.argmax, raw=True))):
        av = np.full(n_, np.nan)
        an = anchor.to_numpy()
        for t in range(n_):
            if an[t] == an[t]:
                s = t - 249 + int(an[t])
                pv = csum_pv[t] - (csum_pv[s - 1] if s > 0 else 0.0)
                vv = csum_v[t] - (csum_v[s - 1] if s > 0 else 0.0)
                av[t] = C[t] / (pv / vv) - 1 if vv > 0 else np.nan
        out[f"avwap_52w_{name}"] = av
    step = 10 ** np.floor(np.log10(c.clip(lower=1))) / 2
    out["round_number_dist"] = ((c / step).round() * step - c) / c
    out["gann_sq9_dist"] = ((np.round(np.sqrt(c) * 4) / 4) ** 2 - c) / c    # nearest 45-degree level on the square of nine
    # ---- Wyckoff-style compression: bands at a 1-year low in width, with volume drying up
    bw = (4 * c.rolling(20).std()) / c.rolling(20).mean()
    out["wyckoff_compression"] = ((bw <= bw.rolling(250).quantile(0.1)) & (v.rolling(5).mean() < 0.8 * v.rolling(50).mean())).astype(float)
    # ---- harmonic: a Gartley on the last confirmed swing points
    out["gartley"] = _gartley(H, L)
    return out


def _gartley(H, L) -> np.ndarray:
    """+1 when the last confirmed alternating swings X-A-B-C-D form a bullish Gartley at D (AB about 0.618 XA, BC
    0.382-0.886 AB, AD about 0.786 XA), -1 for the bearish mirror, else 0. Swings confirm 2 bars after the pivot."""
    n = len(H)
    out = np.zeros(n)
    alt = []                                               # alternating swings (index, price, +1 high / -1 low)
    for t in range(4, n):
        k = t - 2
        for p in ((k, H[k], 1) if H[k] > max(H[k - 2], H[k - 1], H[k + 1], H[k + 2]) else None,
                  (k, L[k], -1) if L[k] < min(L[k - 2], L[k - 1], L[k + 1], L[k + 2]) else None):
            if p is None:
                continue
            if alt and alt[-1][2] == p[2]:
                if (p[2] == 1 and p[1] > alt[-1][1]) or (p[2] == -1 and p[1] < alt[-1][1]):
                    alt[-1] = p
            else:
                alt.append(p)
        if len(alt) < 5:
            continue
        X, A_, B, C_, D = (q[1] for q in alt[-5:])
        xa, ab = A_ - X, B - A_
        if xa == 0 or ab == 0:
            continue
        r_ab, r_bc, r_ad = -ab / xa, -(C_ - B) / ab, (A_ - D) / xa
        if 0.55 <= r_ab <= 0.68 and 0.382 <= r_bc <= 0.886 and 0.75 <= r_ad <= 0.83 and alt[-1][0] >= t - 4:
            out[t] = 1.0 if alt[-1][2] == -1 else -1.0
    return out
