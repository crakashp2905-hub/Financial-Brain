"""Trial 14, ``PRE-REGISTERED intraday chart candle and indicator family``: thirty-five testable candidates (plus the
controls ORB and GAP) on 2016-2026 minute bars of F&O stocks.

Conventions are fixed by the registration (see the note): 5- and 15-minute bars from 09:15; a signal completes at the end of
a bar; entry at the open of the next 5-minute bar; stop floor 0.25%; target 2R unless stated; stop before target when one
bar reaches both; time exit at the 15:10-15:15 bar's close; 10 bp round trip.

``scan_symbol`` produces *signals* only (rule, session, entry bar, direction, stop and target distances in percent of the
entry price). Outcomes come from :func:`simulate`, the one exit model used for real trades and for the random-entry null,
so the two cannot differ in how they exit.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from financial_brain.patterns import detectors2 as D2
from financial_brain.patterns.detectors import Bars, discover

NB = 75                       # five-minute bars in a session
NB15 = 25
LAST = 71                     # bar starting 15:10; its close is 15:15
FLOOR = 0.0025
COST = 0.0010
MAX_SIG = 62                  # last signal bar (completes 14:30)
J_1015 = 11                   # bar completing 10:15
INDEX_MIN_BARS = 60


def bar_of(hhmm: str) -> int:
    """Index of the 5-minute bar *starting* at ``hhmm``."""
    h, m = int(hhmm[:2]), int(hhmm[3:])
    return (h * 60 + m - 555) // 5


# ------------------------------------------------------------------------------------------ session arrays

def session_arrays(m: pd.DataFrame) -> dict:
    """Minute bars (columns ts, open, high, low, close, volume; ts tz-aware) -> per-session 5-minute arrays.

    Only sessions with bars 0-2 present and at least 60 of 75 bars are kept; gaps are filled flat at the previous close
    with zero volume."""
    ts = m["ts"].dt.tz_localize(None)
    mins = ts.dt.hour * 60 + ts.dt.minute - 555
    keep = (mins >= 0) & (mins < 375)
    df = pd.DataFrame({"d": ts[keep].dt.normalize(), "b": (mins[keep] // 5).astype(int), "o": m["open"][keep],
                       "h": m["high"][keep], "l": m["low"][keep], "c": m["close"][keep], "v": m["volume"][keep]})
    g = df.groupby(["d", "b"]).agg(o=("o", "first"), h=("h", "max"), l=("l", "min"), c=("c", "last"), v=("v", "sum"))
    days = g.index.get_level_values(0).unique().sort_values()
    di = days.get_indexer(g.index.get_level_values(0))
    bi = g.index.get_level_values(1).to_numpy()
    shape = (len(days), NB)
    A = {k: np.full(shape, np.nan) for k in "ohlcv"}
    for k in "ohlcv":
        A[k][di, bi] = g[k].to_numpy()
    present = ~np.isnan(A["c"])
    ok = present[:, :3].all(axis=1) & (present.sum(axis=1) >= INDEX_MIN_BARS)
    for k in "ohlcv":
        A[k] = A[k][ok]
    days = days[ok]
    # fill gaps flat
    c = pd.DataFrame(A["c"]).ffill(axis=1).to_numpy()
    A["o"] = np.where(np.isnan(A["o"]), c, A["o"])
    A["h"] = np.where(np.isnan(A["h"]), c, A["h"])
    A["l"] = np.where(np.isnan(A["l"]), c, A["l"])
    A["c"] = c
    A["v"] = np.where(np.isnan(A["v"]), 0.0, A["v"])
    # internal consistency (adjusted data can violate OHLC by rounding)
    A["h"] = np.maximum.reduce([A["h"], A["o"], A["c"]])
    A["l"] = np.minimum.reduce([A["l"], A["o"], A["c"]])
    return {"days": days, **A}


def to15(A: dict) -> dict:
    n = len(A["c"])
    sh = (n, NB15, 3)
    o = A["o"].reshape(sh)[:, :, 0]
    h = A["h"].reshape(sh).max(axis=2)
    lo = A["l"].reshape(sh).min(axis=2)
    c = A["c"].reshape(sh)[:, :, 2]
    v = A["v"].reshape(sh).sum(axis=2)
    return {"o": o, "h": h, "l": lo, "c": c, "v": v}


def _wilder(x: pd.Series, n: int = 14) -> pd.Series:
    return x.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def indicators15(B: dict) -> dict:
    """Causal indicators on the continuous 15-minute series (sessions concatenated). Arrays are (sessions, 25)."""
    shape = B["c"].shape
    c, h, lo = (pd.Series(B[k].ravel()) for k in ("c", "h", "l"))
    pc = c.shift(1)
    tr = pd.concat([h - lo, (h - pc).abs(), (lo - pc).abs()], axis=1).max(axis=1)
    sma = c.rolling(20).mean()
    sd = c.rolling(20).std(ddof=0)
    bw = 4 * sd / sma
    q10 = bw.rolling(120, min_periods=120).quantile(0.10).shift(1)
    up, dn = h.diff(), -lo.diff()
    plus = np.where((up > dn) & (up > 0), up, 0.0)
    minus = np.where((dn > up) & (dn > 0), dn, 0.0)
    atr_w = _wilder(tr)
    pdi = 100 * _wilder(pd.Series(plus)) / atr_w
    mdi = 100 * _wilder(pd.Series(minus)) / atr_w
    adx = _wilder(100 * (pdi - mdi).abs() / (pdi + mdi))
    d = c.diff()
    rsi = 100 - 100 / (1 + _wilder(d.clip(lower=0)) / _wilder((-d).clip(lower=0)))
    ema = {n: c.ewm(span=n, adjust=False, min_periods=n).mean() for n in (8, 13, 21, 34, 55)}
    hh = h.shift(1).rolling(20).max()
    ll = lo.shift(1).rolling(20).min()
    atr15 = tr.rolling(14, min_periods=8).mean()
    # strict 2-2 pivots, known two bars later
    lv, hv = lo.to_numpy(), h.to_numpy()
    n = len(lv)
    pl = np.zeros(n, bool)
    ph = np.zeros(n, bool)
    for k in range(2, n - 2):
        if lv[k] < min(lv[k - 2], lv[k - 1], lv[k + 1], lv[k + 2]):
            pl[k] = True
        if hv[k] > max(hv[k - 2], hv[k - 1], hv[k + 1], hv[k + 2]):
            ph[k] = True
    rs = rsi.to_numpy()
    bull = np.zeros(n, bool)       # flagged at the confirming bar k = p + 2
    bear = np.zeros(n, bool)
    piv_px_l = np.full(n, np.nan)
    piv_px_h = np.full(n, np.nan)
    last_l = last_h = -999
    for k in range(n):
        p = k - 2
        if p >= 0 and pl[p]:
            if 0 < p - last_l <= 40 and lv[p] < lv[last_l] and rs[p] > rs[last_l]:
                bull[k] = True
                piv_px_l[k] = lv[p]
            last_l = p
        if p >= 0 and ph[p]:
            if 0 < p - last_h <= 40 and hv[p] > hv[last_h] and rs[p] < rs[last_h]:
                bear[k] = True
                piv_px_h[k] = hv[p]
            last_h = p
    f = lambda s: np.asarray(s, float).reshape(shape)
    return {"sma20": f(sma), "sd": f(sd), "bw": f(bw), "q10": f(q10), "adx": f(adx), "rsi": f(rsi),
            "e8": f(ema[8]), "e13": f(ema[13]), "e21": f(ema[21]), "e34": f(ema[34]), "e55": f(ema[55]),
            "hh": f(hh), "ll": f(ll), "atr15": f(atr15), "bull_div": bull.reshape(shape), "bear_div": bear.reshape(shape),
            "piv_l": piv_px_l.reshape(shape), "piv_h": piv_px_h.reshape(shape)}


def slot_volume_mean(V: np.ndarray, window: int = 20, min_n: int = 10) -> np.ndarray:
    s = pd.DataFrame(V).shift(1).rolling(window, min_periods=min_n).mean()
    return s.to_numpy()


# ------------------------------------------------------------------------------------------ one session

class Day:
    """Everything a rule may use for one session. Rules read only bars <= the one they signal on."""

    def __init__(self, A, i, B, IND, SV, ctx):
        self.o, self.h, self.l, self.c, self.v = (A[k][i] for k in "ohlcv")
        self.o15, self.h15, self.l15, self.c15, self.v15 = (B[k][i] for k in "ohlcv")
        self.i = i
        self.IND = {k: v[i] for k, v in IND.items()}
        self.sv = SV[i]
        self.pc, self.ph, self.pl = ctx["pc"], ctx["ph"], ctx["pl"]
        self.sma20 = ctx["sma20"]
        self.nifty = ctx["nifty"]                 # dict or None
        self.regime = ctx["regime"]
        o0 = self.o[0]
        self.o0 = o0
        self.gap = o0 / self.pc - 1 if self.pc else np.nan
        self.or_hi = float(self.h[:3].max())
        self.or_lo = float(self.l[:3].min())
        self.or_w = self.or_hi - self.or_lo
        tp = (self.h + self.l + self.c) / 3
        cv = np.cumsum(self.v)
        with np.errstate(invalid="ignore", divide="ignore"):
            self.vwap = np.where(cv > 0, np.cumsum(tp * self.v) / cv, self.c)
        pcl = np.concatenate([[self.pc if self.pc else self.c[0]], self.c[:-1]])
        tr = np.maximum.reduce([self.h - self.l, np.abs(self.h - pcl), np.abs(self.l - pcl)])
        atr = np.full(NB, np.nan)
        for j in range(7, NB):
            atr[j] = tr[max(0, j - 13): j + 1].mean()
        self.atr5 = atr
        self._pats = None

    # ---- helpers
    def entry_ok(self, j: int) -> bool:
        return 2 <= j <= MAX_SIG

    def sig(self, j, d, stop, target="r2", last=LAST, trail=None):
        """Build a signal: entry at the next bar's open; stop floored; target in percent of entry."""
        e = j + 1
        if not (3 <= e <= MAX_SIG + 1) or not np.isfinite(stop):
            return None
        ep = self.o[e]
        sp = abs(ep - stop) / ep
        if (d > 0 and stop >= ep) or (d < 0 and stop <= ep):
            sp = FLOOR                            # stop already through the entry: use the floor distance
        sp = max(sp, FLOOR)
        if target == "r2":
            tp = 2 * sp
        elif target is None:
            tp = np.nan
        else:                                     # an explicit target price
            tp = (target - ep) * d / ep
            if not tp > 0:
                return None
        return {"j": j, "e": e, "dir": d, "ep": ep, "stop_pct": sp, "tgt_pct": tp, "last": last,
                "trail_pct": (trail / ep if trail else np.nan)}

    def formations(self):
        if self._pats is None:
            b = Bars(tuple(self.o), tuple(self.h), tuple(self.l), tuple(self.c))
            fs = list(discover(b))
            for fn in (D2.pennant, D2.channels, D2.cup_handle):
                fs += fn(b)
            b15 = Bars(tuple(self.o15), tuple(self.h15), tuple(self.l15), tuple(self.c15))
            fs15 = list(D2.three_methods(b15))
            self._pats = (fs, fs15)
        return self._pats

    def vol_ok(self, j, mult=1.5):
        s = self.sv[j]
        return bool(np.isfinite(s) and s > 0 and self.v[j] >= mult * s)


def _first(mask: np.ndarray, lo: int, hi: int):
    hi = min(hi, len(mask) - 1)
    if hi < lo:
        return None
    w = np.flatnonzero(mask[lo: hi + 1])
    return lo + int(w[0]) if len(w) else None


# ------------------------------------------------------------------------------------------ the rules

def orb(D: Day, j0=3, j1=21, allow_long=True, allow_short=True):
    up = (D.c > D.or_hi) if allow_long else np.zeros(NB, bool)
    dn = (D.c < D.or_lo) if allow_short else np.zeros(NB, bool)
    ju, jd = _first(up, j0, j1), _first(dn, j0, j1)
    if ju is None and jd is None:
        return None
    if jd is None or (ju is not None and ju <= jd):
        return ju, 1, D.or_lo
    return jd, -1, D.or_hi


def r_L0(D):
    r = orb(D)
    return D.sig(r[0], r[1], r[2]) if r else None


def r_G0(D, thr=0.015, last_override=None):
    if not (abs(D.gap) >= thr):
        return None
    if D.gap > 0:
        j = _first(D.c < D.o0, 1, MAX_SIG)
        if j is None:
            return None
        return D.sig(j, -1, float(D.h[: j + 1].max()), target=D.pc)
    j = _first(D.c > D.o0, 1, MAX_SIG)
    if j is None:
        return None
    return D.sig(j, 1, float(D.l[: j + 1].min()), target=D.pc)


def r_L6(D):
    return r_G0(D, 0.025)


def _nifty_ok(D):
    return D.nifty is not None


def r_L1(D):
    if not _nifty_ok(D) or not np.isfinite(D.gap):
        return None
    n = D.nifty
    nu = n["o30"] > n["o0"]
    al = D.gap > 0 and nu
    as_ = D.gap < 0 and (not nu) and n["o30"] < n["o0"]
    r = orb(D, allow_long=al, allow_short=as_)
    return D.sig(r[0], r[1], r[2]) if r else None


def r_L2(D):
    if not _nifty_ok(D) or not np.isfinite(D.sma20) or not np.isfinite(D.nifty["trend"]):
        return None
    st = D.pc - D.sma20
    nt = D.nifty["trend"]
    al = st > 0 and nt > 0
    as_ = st < 0 and nt < 0
    if not (al or as_):
        return None
    r = orb(D, allow_long=al, allow_short=as_)
    return D.sig(r[0], r[1], r[2]) if r else None


def r_L3(D):
    w = D.or_w / D.o0
    if not (0.005 <= w <= 0.02):
        return None
    return r_L0(D)


def r_L4(D):
    if D.or_w / D.o0 < 0.004:
        return None
    return r_L0(D)


def r_L5(D):
    r = orb(D)
    if not r:
        return None
    return D.sig(r[0], r[1], r[2], target=None, last=bar_of("12:55"), trail=D.or_w)


def r_L7(D):
    if not (abs(D.gap) >= 0.01):
        return None
    r = orb(D, allow_long=D.gap > 0, allow_short=D.gap < 0)
    return D.sig(r[0], r[1], r[2]) if r else None


def r_L8(D):
    up = (D.h > D.or_hi) & (D.c < D.or_hi)
    dn = (D.l < D.or_lo) & (D.c > D.or_lo)
    ju, jd = _first(up, 3, MAX_SIG), _first(dn, 3, MAX_SIG)
    if ju is None and jd is None:
        return None
    if jd is None or (ju is not None and ju <= jd):
        return D.sig(ju, -1, D.h[ju])
    return D.sig(jd, 1, D.l[jd])


def r_L9(D):
    j_up = _first(D.c > D.ph, 3, MAX_SIG)
    j_dn = _first(D.c < D.pl, 3, MAX_SIG)
    cand = [(j, d) for j, d in ((j_up, 1), (j_dn, -1)) if j is not None]
    if not cand:
        return None
    j0, d = min(cand)
    lvl = D.ph if d > 0 else D.pl
    for k in range(j0 + 1, min(j0 + 12, MAX_SIG) + 1):
        if d > 0 and D.l[k] <= lvl * 1.0005 and D.c[k] > lvl:
            return D.sig(k, 1, D.l[k])
        if d < 0 and D.h[k] >= lvl * 0.9995 and D.c[k] < lvl:
            return D.sig(k, -1, D.h[k])
    return None


def r_L10(D):
    if not (abs(D.gap) >= 0.005):
        return None
    if D.gap > 0:
        if (D.l[:12] > D.pc).all() and D.c[11] > D.o0:
            return D.sig(11, 1, D.pc)
    else:
        if (D.h[:12] < D.pc).all() and D.c[11] < D.o0:
            return D.sig(11, -1, D.pc)
    return None


def r_L11(D):
    hi, lo = D.h[:6].max(), D.l[:6].min()
    up, dn = hi >= D.o0 * 1.015, lo <= D.o0 * 0.985
    if up == dn:
        return None
    for j in range(6, MAX_SIG + 1):
        if up:
            if D.h[j] <= D.h[:j].max() and D.c[j] < D.l[j - 1]:
                return D.sig(j, -1, float(D.h[: j + 1].max()))
        else:
            if D.l[j] >= D.l[:j].min() and D.c[j] > D.h[j - 1]:
                return D.sig(j, 1, float(D.l[: j + 1].min()))
    return None


def r_L12(D):
    if not np.isfinite(D.sma20) or D.pc == D.sma20:
        return None
    if D.pc > D.sma20:
        j = _first((D.l <= D.vwap) & (D.c > D.vwap), bar_of("10:00"), MAX_SIG)
        return D.sig(j, 1, D.l[j]) if j is not None else None
    j = _first((D.h >= D.vwap) & (D.c < D.vwap), bar_of("10:00"), MAX_SIG)
    return D.sig(j, -1, D.h[j]) if j is not None else None


def r_L13(D):
    if not _nifty_ok(D):
        return None
    n = D.nifty
    rs = (D.c[J_1015] / D.o0 - 1) - (n["c1015"] / n["o0"] - 1)
    a = D.atr5[J_1015]
    if not np.isfinite(a):
        return None
    if rs >= 0.01:
        return D.sig(J_1015, 1, D.o[J_1015 + 1] - 3 * a)
    if rs <= -0.01:
        return D.sig(J_1015, -1, D.o[J_1015 + 1] + 3 * a)
    return None


def r_L15(D):
    rg = D.regime
    if rg == "RISK_ON":
        r = orb(D, allow_short=False)
    elif rg == "RISK_OFF":
        r = orb(D, allow_long=False)
    elif rg in ("NEUTRAL", "NARROW"):
        return r_L8(D)
    else:
        return None
    return D.sig(r[0], r[1], r[2]) if r else None


def r_L18(D):
    r = orb(D, 3, J_1015)
    return D.sig(r[0], r[1], r[2]) if r else None


# ---- patterns

def _formation_signals(D: Day, match):
    fs, _ = D.formations()
    out = []
    for f in fs:
        if f.confirmed_index is None or f.confirmed_direction not in ("bullish", "bearish"):
            continue
        if not match(f.detector):
            continue
        j = f.confirmed_index
        if not (3 <= j + 1 <= MAX_SIG + 1) or j < 2:
            continue
        d = 1 if f.confirmed_direction == "bullish" else -1
        out.append((j, d, f))
    out.sort(key=lambda t: (t[0], t[1]))
    return out


def _pat(match):
    def rule(D):
        s = _formation_signals(D, match)
        if not s:
            return None
        j, d, f = s[0]
        st = float(D.l[f.start_index: j + 1].min()) if d > 0 else float(D.h[f.start_index: j + 1].max())
        return D.sig(j, d, st)
    return rule


r_P1 = _pat(lambda n: n == "rectangle")
r_P2 = _pat(lambda n: n.startswith("triangle"))
r_P3 = _pat(lambda n: n.startswith("flag") or n.startswith("pennant"))
r_P4 = _pat(lambda n: n.startswith("double_"))
r_P5 = _pat(lambda n: n.startswith("head") or n.startswith("inverse_head"))
r_P6 = _pat(lambda n: n.startswith("cup"))
r_P7 = _pat(lambda n: n.startswith("channel"))


def r_P9(D):
    fs = _formation_signals(D, lambda n: n == "rectangle" or n.startswith("triangle") or n.startswith("flag")
                            or n.startswith("pennant"))
    best = None
    for jc, d, f in fs:
        for k in range(jc + 1, min(jc + 12, MAX_SIG) + 1):
            a = D.atr5[k]
            if not np.isfinite(a):
                continue
            if d > 0 and D.l[k] <= D.c[jc] - 0.5 * a and D.c[k] > D.l[jc]:
                s = D.sig(k, 1, float(D.l[jc: k + 1].min()))
            elif d < 0 and D.h[k] >= D.c[jc] + 0.5 * a and D.c[k] < D.h[jc]:
                s = D.sig(k, -1, float(D.h[jc: k + 1].max()))
            else:
                continue
            if s and (best is None or s["j"] < best["j"]):
                best = s
            break
    return best


def r_P8(D):
    if not (abs(D.gap) >= 0.005):
        return None
    if D.gap > 0:
        j = _first(D.c <= D.pc, 2, 11)
        return D.sig(j, -1, float(D.h[: j + 1].max())) if j is not None else None
    j = _first(D.c >= D.pc, 2, 11)
    return D.sig(j, 1, float(D.l[: j + 1].min())) if j is not None else None


# ---- candles on 15-minute bars. k = 15-minute bar index; completes at j = 3k + 2

def _body(D, k):
    return abs(D.c15[k] - D.o15[k])


def _hammer(D, k):
    rng = D.h15[k] - D.l15[k]
    if rng <= 0:
        return False
    body = _body(D, k)
    lower = min(D.o15[k], D.c15[k]) - D.l15[k]
    upper = D.h15[k] - max(D.o15[k], D.c15[k])
    return lower >= 2 * max(body, 1e-9) and upper <= 0.1 * rng


def _star(D, k):
    rng = D.h15[k] - D.l15[k]
    if rng <= 0:
        return False
    body = _body(D, k)
    lower = min(D.o15[k], D.c15[k]) - D.l15[k]
    upper = D.h15[k] - max(D.o15[k], D.c15[k])
    return upper >= 2 * max(body, 1e-9) and lower <= 0.1 * rng


def _bull_engulf(D, k):
    return (k >= 1 and D.c15[k - 1] < D.o15[k - 1] and D.c15[k] > D.o15[k]
            and D.o15[k] <= D.c15[k - 1] and D.c15[k] >= D.o15[k - 1])


def _bear_engulf(D, k):
    return (k >= 1 and D.c15[k - 1] > D.o15[k - 1] and D.c15[k] < D.o15[k]
            and D.o15[k] >= D.c15[k - 1] and D.c15[k] <= D.o15[k - 1])


def _dark_cloud(D, k):
    return (k >= 1 and D.c15[k - 1] > D.o15[k - 1] and D.o15[k] > D.h15[k - 1]
            and D.o15[k - 1] < D.c15[k] < (D.o15[k - 1] + D.c15[k - 1]) / 2)


def _morning(D, k):
    if k < 2:
        return False
    b1, b2 = _body(D, k - 2), _body(D, k - 1)
    a = D.IND["atr15"][k - 2]
    return bool(np.isfinite(a) and D.c15[k - 2] < D.o15[k - 2] and b1 >= a and b2 <= 0.3 * b1
                and D.c15[k] > D.o15[k] and D.c15[k] >= (D.o15[k - 2] + D.c15[k - 2]) / 2)


def _evening(D, k):
    if k < 2:
        return False
    b1, b2 = _body(D, k - 2), _body(D, k - 1)
    a = D.IND["atr15"][k - 2]
    return bool(np.isfinite(a) and D.c15[k - 2] > D.o15[k - 2] and b1 >= a and b2 <= 0.3 * b1
                and D.c15[k] < D.o15[k] and D.c15[k] <= (D.o15[k - 2] + D.c15[k - 2]) / 2)


def r_P10(D):
    for k in range(1, 20):
        if _bull_engulf(D, k) and D.l15[k] <= D.or_lo * 1.002 and D.c15[k + 1] > D.c15[k]:
            return D.sig(3 * (k + 1) + 2, 1, D.l15[k])
        if (_bear_engulf(D, k) or _dark_cloud(D, k)) and D.h15[k] >= D.or_hi * 0.998 and D.c15[k + 1] < D.c15[k]:
            return D.sig(3 * (k + 1) + 2, -1, D.h15[k])
    return None


def r_P11(D):
    for k in range(1, 21):
        j = 3 * k + 2
        vw = D.vwap[j]
        if _hammer(D, k) and (abs(D.l15[k] - vw) / vw <= 0.0015 or abs(D.l15[k] - D.pl) / D.pl <= 0.0015):
            return D.sig(j, 1, D.l15[k])
        if _star(D, k) and (abs(D.h15[k] - vw) / vw <= 0.0015 or abs(D.h15[k] - D.ph) / D.ph <= 0.0015):
            return D.sig(j, -1, D.h15[k])
    return None


def r_P12(D):
    move = D.c[J_1015] / D.o0 - 1
    for k in range(4, 21):
        j = 3 * k + 2
        if move <= -0.005 and _morning(D, k):
            return D.sig(j, 1, float(D.l15[k - 2: k + 1].min()))
        if move >= 0.005 and _evening(D, k):
            return D.sig(j, -1, float(D.h15[k - 2: k + 1].max()))
    return None


def r_P13(D):
    _, fs15 = D.formations()
    out = []
    for f in fs15:
        if f.confirmed_index is None or f.confirmed_direction not in ("bullish", "bearish"):
            continue
        j = 3 * f.confirmed_index + 2
        if 2 <= j <= MAX_SIG:
            out.append((j, f))
    if not out:
        return None
    j, f = min(out, key=lambda t: t[0])
    k, s = f.confirmed_index, f.start_index
    if f.confirmed_direction == "bullish":
        return D.sig(j, 1, float(D.l15[s: k + 1].min()))
    return D.sig(j, -1, float(D.h15[s: k + 1].max()))


def r_P14(D):
    hi, lo = D.h[:12].max(), D.l[:12].min()
    for k in range(4, 21):
        j = 3 * k + 2
        if D.l15[k] < lo and D.c15[k] > lo and _hammer(D, k):
            return D.sig(j, 1, D.l15[k])
        if D.h15[k] > hi and D.c15[k] < hi and _star(D, k):
            return D.sig(j, -1, D.h15[k])
    return None


# ---- indicator rules on 15-minute bars

def r_P15(D):
    I = D.IND
    for k in range(1, 21):
        j = 3 * k + 2
        if not (np.isfinite(I["bw"][k - 1]) and np.isfinite(I["q10"][k - 1]) and np.isfinite(I["sma20"][k])):
            continue
        if I["bw"][k - 1] <= I["q10"][k - 1]:
            up = I["sma20"][k] + 2 * I["sd"][k]
            dn = I["sma20"][k] - 2 * I["sd"][k]
            if D.c15[k] > up:
                return D.sig(j, 1, I["sma20"][k])
            if D.c15[k] < dn:
                return D.sig(j, -1, I["sma20"][k])
    return None


def r_P16(D):
    I = D.IND
    for k in range(0, 21):
        j = 3 * k + 2
        if not (np.isfinite(I["hh"][k]) and np.isfinite(I["adx"][k])) or I["adx"][k] < 25:
            continue
        mid = (I["hh"][k] + I["ll"][k]) / 2
        if D.c15[k] > I["hh"][k]:
            return D.sig(j, 1, mid)
        if D.c15[k] < I["ll"][k]:
            return D.sig(j, -1, mid)
    return None


def r_P17(D):
    I = D.IND
    for k in range(2, 21):
        j = 3 * k + 2
        if I["bull_div"][k]:
            px = I["piv_l"][k]
            if min(abs(px - D.or_lo) / D.or_lo, abs(px - D.pl) / D.pl) <= 0.0015:
                return D.sig(j, 1, px)
        if I["bear_div"][k]:
            px = I["piv_h"][k]
            if min(abs(px - D.or_hi) / D.or_hi, abs(px - D.ph) / D.ph) <= 0.0015:
                return D.sig(j, -1, px)
    return None


def r_P18(D):
    I = D.IND
    for k in range(0, 21):
        j = 3 * k + 2
        e8, e13, e21, e34, e55 = (I[x][k] for x in ("e8", "e13", "e21", "e34", "e55"))
        if not np.isfinite(e55):
            continue
        if e8 > e13 > e21 > e34 > e55 and D.l15[k] <= e21 and D.c15[k] > e8:
            return D.sig(j, 1, D.l15[k])
        if e8 < e13 < e21 < e34 < e55 and D.h15[k] >= e21 and D.c15[k] < e8:
            return D.sig(j, -1, D.h15[k])
    return None


def r_P19(D):
    for j in range(bar_of("10:15"), MAX_SIG + 1):
        hi, lo = D.h[j - 12: j].max(), D.l[j - 12: j].min()
        if D.c[j] > hi and D.vol_ok(j):
            return D.sig(j, 1, lo)
        if D.c[j] < lo and D.vol_ok(j):
            return D.sig(j, -1, hi)
    return None


def r_P20(D):
    fs, _ = D.formations()
    pat = [(f.confirmed_index, 1 if f.confirmed_direction == "bullish" else -1) for f in fs
           if f.confirmed_index is not None and f.confirmed_direction in ("bullish", "bearish")
           and (f.detector == "rectangle" or f.detector.startswith(("triangle", "flag", "pennant", "double_", "head",
                                                                    "inverse_head", "cup", "channel")))]
    I = D.IND
    for k in range(1, 21):
        j = 3 * k + 2
        sl = ss = 0
        # 1 pattern
        sl += any(d > 0 and j - 11 <= ci <= j for ci, d in pat)
        ss += any(d < 0 and j - 11 <= ci <= j for ci, d in pat)
        # 2 candle
        sl += bool(_bull_engulf(D, k) or _hammer(D, k) or _morning(D, k))
        ss += bool(_bear_engulf(D, k) or _dark_cloud(D, k) or _star(D, k) or _evening(D, k))
        # 3 trend side
        e21 = I["e21"][k]
        if np.isfinite(e21):
            sl += bool(D.c15[k] > D.vwap[j] and D.c15[k] > e21)
            ss += bool(D.c15[k] < D.vwap[j] and D.c15[k] < e21)
        # 4 level
        sl += bool(abs(D.l15[k] - D.pl) / D.pl <= 0.002 or abs(D.l15[k] - D.or_lo) / D.or_lo <= 0.002)
        ss += bool(abs(D.h15[k] - D.ph) / D.ph <= 0.002 or abs(D.h15[k] - D.or_hi) / D.or_hi <= 0.002)
        # 5 volume
        sm = np.nansum(D.sv[3 * k: 3 * k + 3])
        if np.isfinite(sm) and sm > 0 and D.v15[k] >= 1.5 * sm:
            sl += 1
            ss += 1
        # 6 index
        if D.nifty is not None:
            nc = D.nifty["c"][j]
            sl += bool(nc > D.nifty["o0"])
            ss += bool(nc < D.nifty["o0"])
        if (sl >= 3) != (ss >= 3):
            a = D.atr5[j]
            if not np.isfinite(a):
                continue
            ep = D.o[j + 1]
            return D.sig(j, 1 if sl >= 3 else -1, ep - 3 * a if sl >= 3 else ep + 3 * a)
    return None


RULES = {
    "L0": r_L0, "G0": r_G0,
    "L1": r_L1, "L2": r_L2, "L3": r_L3, "L4": r_L4, "L5": r_L5, "L6": r_L6, "L7": r_L7, "L8": r_L8, "L9": r_L9,
    "L10": r_L10, "L11": r_L11, "L12": r_L12, "L13": r_L13, "L15": r_L15, "L18": r_L18,
    "P1": r_P1, "P2": r_P2, "P3": r_P3, "P4": r_P4, "P5": r_P5, "P6": r_P6, "P7": r_P7, "P8": r_P8, "P9": r_P9,
    "P10": r_P10, "P11": r_P11, "P12": r_P12, "P13": r_P13, "P14": r_P14, "P15": r_P15, "P16": r_P16,
    "P17": r_P17, "P18": r_P18, "P19": r_P19, "P20": r_P20,
}
CONTROLS = ("L0", "G0")
UNTESTABLE = {"L14": "no minute-aligned open-interest history", "L16": "needs a model fitted on the same data",
              "L17": "announcement times not aligned to minute bars", "L19": "no option quote history",
              "L20": "depth archive holds weeks; licence unclear"}
FAMILY = [r for r in RULES if r not in CONTROLS]          # 35


def evaluate_day(D: Day) -> dict:
    out = {}
    for name, fn in RULES.items():
        try:
            s = fn(D)
        except (IndexError, ZeroDivisionError, FloatingPointError, ValueError):
            s = None
        if s is not None and D.entry_ok(s["j"]):
            out[name] = s
    return out


# ------------------------------------------------------------------------------------------ exit model

def simulate(O, H, L, C, e, d, stop_pct, tgt_pct, last, ep=None):
    """Vectorised exits for ``n`` trades. Arrays (n, 75); ``e`` entry bar, ``d`` direction (+1/-1), distances in percent
    of the entry price, ``last`` the time-exit bar. Returns (gross return, exit bar, reason 0 time / 1 stop / 2 target).

    Stop is taken first when a bar reaches both; a bar that opens beyond the stop exits at its open (and likewise beyond
    the target, in the trade's favour)."""
    n = len(e)
    idx = np.arange(NB)[None, :]
    if ep is None:
        ep = O[np.arange(n), e]
    stop = ep * (1 - d * stop_pct)
    tgt = ep * (1 + d * np.where(np.isfinite(tgt_pct), tgt_pct, np.inf))
    live = (idx >= e[:, None]) & (idx <= last[:, None])
    dd = d[:, None]
    stop_hit = live & np.where(dd > 0, L <= stop[:, None], H >= stop[:, None])
    tgt_hit = live & np.where(dd > 0, H >= tgt[:, None], L <= tgt[:, None]) & np.isfinite(tgt)[:, None]
    big = NB + 1
    ks = np.where(stop_hit.any(1), stop_hit.argmax(1), big)
    kt = np.where(tgt_hit.any(1), tgt_hit.argmax(1), big)
    rows = np.arange(n)
    ks_c, kt_c = np.minimum(ks, NB - 1), np.minimum(kt, NB - 1)
    px_stop = np.where(d > 0, np.minimum(O[rows, ks_c], stop), np.maximum(O[rows, ks_c], stop))
    px_stop = np.where(ks_c == e, stop, px_stop)
    px_tgt = np.where(d > 0, np.maximum(O[rows, kt_c], tgt), np.minimum(O[rows, kt_c], tgt))
    px_tgt = np.where(kt_c == e, tgt, px_tgt)
    take_stop = ks <= kt
    hit = (ks < big) | (kt < big)
    kx = np.where(hit, np.where(take_stop, ks, kt), last)
    px = np.where(hit, np.where(take_stop, px_stop, px_tgt), C[rows, np.minimum(last, NB - 1)])
    reason = np.where(hit, np.where(take_stop, 1, 2), 0)
    return d * (px / ep - 1), kx, reason


def simulate_trail(o, h, l, c, e, d, stop_pct, trail_pct, last, ep):
    """Scalar exit for the trailing-stop rule (L5): stop starts at ``stop_pct`` and follows the running extreme by
    ``trail_pct`` of the entry price."""
    w = trail_pct * ep
    stop = ep * (1 - d * stop_pct)
    ext = ep
    for j in range(e, last + 1):
        if d > 0:
            if j > e and o[j] <= stop:
                return d * (o[j] / ep - 1)
            if l[j] <= stop:
                return d * (stop / ep - 1)
            ext = max(ext, h[j])
            stop = max(stop, ext - w)
        else:
            if j > e and o[j] >= stop:
                return d * (o[j] / ep - 1)
            if h[j] >= stop:
                return d * (stop / ep - 1)
            ext = min(ext, l[j])
            stop = min(stop, ext + w)
    return d * (c[last] / ep - 1)


# ------------------------------------------------------------------------------------------ one symbol

def scan_symbol(sym: str, m: pd.DataFrame, fno_days: set, nifty: dict, regimes: pd.Series) -> dict:
    """Signals for every rule plus the compact grids the null needs. ``nifty``: output of :func:`nifty_context`."""
    A = session_arrays(m)
    days = A["days"]
    if len(days) < 120:
        return {"sym": sym, "signals": pd.DataFrame(), "days": days, "grid": None}
    B = to15(A)
    IND = indicators15(B)
    SV = slot_volume_mean(A["v"])
    dc = A["c"][:, -1]
    pc = np.concatenate([[np.nan], dc[:-1]])
    ph = np.concatenate([[np.nan], A["h"].max(axis=1)[:-1]])
    pl = np.concatenate([[np.nan], A["l"].min(axis=1)[:-1]])
    sma = pd.Series(dc).rolling(20).mean().shift(1).to_numpy()
    rows = []
    for i, d in enumerate(days):
        if d not in fno_days or not np.isfinite(pc[i]) or pc[i] <= 0:
            continue
        ctx = {"pc": pc[i], "ph": ph[i], "pl": pl[i], "sma20": sma[i], "nifty": nifty.get(d),
               "regime": regimes.get(d)}
        D = Day(A, i, B, IND, SV, ctx)
        for rule, s in evaluate_day(D).items():
            rows.append({"rule": rule, "sym": sym, "di": i, "date": d, **s})
    sig = pd.DataFrame(rows)
    grid = {k: A[k].astype(np.float32) for k in "ohlc"}
    value = (A["c"] * A["v"]).sum(axis=1)
    return {"sym": sym, "signals": sig, "days": days, "grid": grid, "value": value}


def nifty_context(df: pd.DataFrame) -> dict:
    """Per-session Nifty context from its minute bars: open at 09:15, open at 09:30, close at 10:15, the close per
    5-minute bar, and the sign of previous close minus its 20-session mean."""
    A = session_arrays(df)
    days = A["days"]
    dc = A["c"][:, -1]
    pcl = np.concatenate([[np.nan], dc[:-1]])
    sma = pd.Series(dc).rolling(20).mean().shift(1).to_numpy()
    out = {}
    for i, d in enumerate(days):
        out[d] = {"o0": A["o"][i, 0], "o30": A["o"][i, 3], "c1015": A["c"][i, J_1015], "c": A["c"][i],
                  "trend": (pcl[i] - sma[i]) if np.isfinite(sma[i]) else np.nan}
    return out


def outcomes(sig: pd.DataFrame, grids: dict) -> pd.DataFrame:
    """Gross and net return per signal. ``grids``: sym -> {o,h,l,c} float arrays (sessions, 75)."""
    if sig.empty:
        return sig.assign(gross=[], net=[])
    gross = np.full(len(sig), np.nan)
    for sym, idx in sig.groupby("sym").indices.items():
        g = grids[sym]
        s = sig.iloc[idx]
        di = s["di"].to_numpy()
        O, H, L, C = (g[k][di].astype(float) for k in "ohlc")
        gr, _kx, _r = simulate(O, H, L, C, s["e"].to_numpy(), s["dir"].to_numpy(), s["stop_pct"].to_numpy(),
                               s["tgt_pct"].to_numpy(), s["last"].to_numpy(), s["ep"].to_numpy())
        gross[idx] = gr
        tr = np.flatnonzero(np.isfinite(s["trail_pct"].to_numpy()))
        for t in tr:
            r = s.iloc[t]
            gross[idx[t]] = simulate_trail(O[t], H[t], L[t], C[t], int(r["e"]), int(r["dir"]), r["stop_pct"],
                                           r["trail_pct"], int(r["last"]), r["ep"])
    out = sig.copy()
    out["gross"] = gross
    out["net"] = gross - COST
    return out
