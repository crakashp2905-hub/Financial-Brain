"""Edge candidates for every mode, as pre-registered in ``PRE-REGISTERED edges for every mode``.

Each function returns a period series (index = period start date) of **net** returns in the unit the registration
fixes (day, week, month or event); ``judge`` applies the bar: t >= 2 with a positive mean in both 2016-2021 and
2022-2026 (or the two halves of the minute sample).

    S1 no_news_reversal        S2 earnings_reaction_drift
    P1 residual_momentum       P2 vol_managed            P3 frog_in_the_pan      (plus plain momentum for comparison)
    F1/F2 oi_buildup           Z1 turn_of_month          Z2 pre_holiday          I2 intraday_seasonality
    I1 index_intraday_momentum (needs index minute bars)
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

RF = 0.065
INDEX_RT = 0.0004           #: Nifty futures round trip
STOCK_FUT_RT = 0.0010       #: stock futures round trip
INTRADAY_RT = 0.00085 + 0.0005
ROUND_TRIP = {"mega": 0.0025, "large": 0.0030, "mid": 0.0045, "small": 0.0060}
NEWS_TYPES = ("RESULTS", "BOARD_OUTCOME", "PRESS_RELEASE", "SUBSTANTIAL_ACQUISITION", "MANAGEMENT_CHANGE",
              "INVESTOR_PRESENTATION")
SPLIT = date(2022, 1, 1)


# ------------------------------------------------------------------------------------------------- data

def load(rcon, start: str = "2015-01-01", n: int = 500) -> dict:
    """Daily panels for the names that were ever in the ``n`` most liquid: close (adjusted), adv20, the universe
    mask (top ``n`` by adv20 each day) and the round-trip cost of each name each day."""
    df = rcon.execute(f"""
        WITH r AS (SELECT lineage, business_date, adv20,
                          ROW_NUMBER() OVER (PARTITION BY business_date ORDER BY adv20 DESC) AS rk
                   FROM features WHERE business_date >= ? AND adv20 > 0)
        SELECT a.business_date AS d, a.lineage, a.close_adj AS c, r.adv20, r.rk
        FROM adjusted_prices a JOIN r USING (lineage, business_date)
        WHERE a.lineage IN (SELECT DISTINCT lineage FROM r WHERE rk <= {n}) AND a.close_adj > 0""", [start]).df()
    df["d"] = pd.to_datetime(df["d"])
    C = df.pivot(index="d", columns="lineage", values="c").sort_index()
    A = df.pivot(index="d", columns="lineage", values="adv20").reindex(C.index)
    K = df.pivot(index="d", columns="lineage", values="rk").reindex(C.index)
    U = K <= n
    a = A.to_numpy()
    cost = pd.DataFrame(np.select([a >= 5e9, a >= 1e9, a >= 2e8, a > 0],
                                  [ROUND_TRIP["mega"], ROUND_TRIP["large"], ROUND_TRIP["mid"], ROUND_TRIP["small"]], np.nan),
                        index=A.index, columns=A.columns)
    return {"C": C, "A": A, "U": U, "K": K, "cost": cost}


def fwd(C: pd.DataFrame, k: int) -> pd.DataFrame:
    return C.shift(-k) / C - 1


def _weights(sel: pd.Series | list) -> pd.Series:
    sel = list(sel)
    return pd.Series(1.0 / len(sel), index=sel) if sel else pd.Series(dtype=float)


def selection_series(P: dict, picks: dict, k: int, *, bench: str = "universe") -> pd.DataFrame:
    """``picks``: rebalance date -> names (equal weight), held ``k`` sessions. Per period: the gross return, the
    universe's, and the cost of the weight changed (one-way half of each name's round trip)."""
    C, U, cost = P["C"], P["U"], P["cost"]
    F = fwd(C, k)
    rows, prev = [], pd.Series(dtype=float)
    for d, names in sorted(picks.items()):
        if d not in F.index:
            continue
        missing = [x for x in names if x not in F.columns or not np.isfinite(F.at[d, x])]
        if missing:
            raise ValueError(f"unresolved future return for selected holding(s) on {d}: {missing[:5]}")
        w = _weights(names)
        if w.empty:
            continue
        uni = U.loc[d]
        ub = F.loc[d, uni[uni].index].mean()
        g = float((w * F.loc[d, w.index]).sum())
        dw = w.sub(prev, fill_value=0).abs()
        c = float((dw * cost.loc[d].reindex(dw.index).fillna(ROUND_TRIP["mid"]) / 2).sum())
        rows.append({"d": d, "gross": g, "bench": float(ub), "cost": c, "net_excess": g - c - float(ub), "net": g - c, "n": len(w)})
        prev = w * (1 + F.loc[d, w.index].fillna(0))
        prev = prev / prev.sum()
    return pd.DataFrame(rows).set_index("d") if rows else pd.DataFrame()


def judge(s: pd.Series, split=SPLIT, *, periods_per_year: float | None = None) -> dict:
    """The registered bar on a period series of net returns."""
    s = s.dropna()
    s.index = pd.to_datetime(s.index)
    a, b = s[s.index < pd.Timestamp(split)], s[s.index >= pd.Timestamp(split)]
    t = lambda x: float(x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))) if len(x) > 2 and x.std() > 0 else float("nan")  # noqa: E731
    out = {"n_a": len(a), "mean_a": float(a.mean()) if len(a) else np.nan, "t_a": t(a),
           "n_b": len(b), "mean_b": float(b.mean()) if len(b) else np.nan, "t_b": t(b)}
    out["pass"] = bool(out["t_a"] >= 2 and out["t_b"] >= 2)
    if periods_per_year:
        out["sharpe_b"] = float(b.mean() / b.std() * np.sqrt(periods_per_year)) if len(b) > 2 else np.nan
    return out


def every(index: pd.DatetimeIndex, k: int, start: str = "2016-01-01") -> list:
    idx = index[index >= pd.Timestamp(start)]
    return list(idx[::k])


def month_ends(index: pd.DatetimeIndex, start: str = "2016-01-01") -> list:
    s = pd.Series(index, index=index)
    me = s.groupby(index.to_period("M")).max()
    return [d for d in me if d >= pd.Timestamp(start)][:-1]


# ------------------------------------------------------------------------------------------------- swing

def news_days(rcon, P: dict) -> pd.DataFrame:
    """(d, lineage) -> True where a material announcement landed."""
    q = f"""SELECT DISTINCT a.business_date AS d, p.lineage FROM announcements a
            JOIN (SELECT DISTINCT isin, lineage FROM adjusted_prices WHERE isin IS NOT NULL) p USING (isin)
            WHERE a.event_type IN ({','.join("'" + x + "'" for x in NEWS_TYPES)}) AND a.business_date >= '2015-06-01'"""
    nd = rcon.execute(q).df()
    nd["d"] = pd.to_datetime(nd["d"])
    M = pd.DataFrame(False, index=P["C"].index, columns=P["C"].columns)
    nd = nd[nd["lineage"].isin(M.columns)]
    # an announcement on a non-session day counts for the next session
    pos = M.index.searchsorted(nd["d"])
    ok = pos < len(M.index)
    for i, lin in zip(pos[ok], nd["lineage"][ok]):
        M.iat[i, M.columns.get_loc(lin)] = True
    return M


def no_news_reversal(P: dict, news: pd.DataFrame, k: int = 5) -> pd.DataFrame:
    """S1: bottom decile of 5-session market-adjusted return among names with no material news in those sessions."""
    C, U = P["C"], P["U"]
    R5 = C / C.shift(k) - 1
    N5 = news.rolling(k, min_periods=1).max().astype(bool)
    picks, picks_news = {}, {}
    for d in every(C.index, k):
        uni = U.loc[d][U.loc[d]].index
        r = R5.loc[d, uni].dropna()
        r = r - r.mean()
        cut = r.quantile(0.10)
        losers = r[r <= cut].index
        quiet = [x for x in losers if not N5.at[d, x]]
        picks[d] = quiet
        picks_news[d] = [x for x in losers if N5.at[d, x]]
    return selection_series(P, picks, k), selection_series(P, picks_news, k)


def earnings_reaction_drift(rcon, P: dict, k: int = 20) -> pd.DataFrame:
    """S2: per RESULTS announcement, the market-adjusted reaction (close before -> close of the session after);
    top quintile within its month, bought at the following close, held ``k`` sessions. One row per event;
    ``monthly`` averages by entry month."""
    C, U, cost = P["C"], P["U"], P["cost"]
    ev = rcon.execute("""SELECT DISTINCT a.business_date AS d, p.lineage FROM announcements a
                         JOIN (SELECT DISTINCT isin, lineage FROM adjusted_prices WHERE isin IS NOT NULL) p USING (isin)
                         WHERE a.event_type = 'RESULTS' AND a.business_date >= '2015-06-01'""").df()
    ev["d"] = pd.to_datetime(ev["d"])
    ev = ev[ev["lineage"].isin(C.columns)]
    idx = C.index
    pos = idx.searchsorted(ev["d"])                       # first session on/after the announcement
    ev = ev.assign(p0=pos)
    ev = ev[(ev["p0"] >= 1) & (ev["p0"] + 2 + k < len(idx))]
    ew = C.pct_change(fill_method=None).where(U).mean(axis=1)
    cum = (1 + ew.fillna(0)).cumprod()
    rows = []
    for lin, p0 in zip(ev["lineage"], ev["p0"]):
        before, after, entry, exit_ = idx[p0 - 1], idx[p0 + 1], idx[p0 + 2], idx[p0 + 2 + k]
        if not U.at[entry, lin]:
            continue
        c0, c1, ce, cx = C.at[before, lin], C.at[after, lin], C.at[entry, lin], C.at[exit_, lin]
        if not all(np.isfinite([c0, c1, ce, cx])):
            continue
        react = c1 / c0 - 1 - (cum[after] / cum[before] - 1)
        ex = cx / ce - 1 - (cum[exit_] / cum[entry] - 1)
        rows.append({"d": entry, "lineage": lin, "react": react, "excess": ex, "cost": cost.at[entry, lin]})
    E = pd.DataFrame(rows).drop_duplicates(["d", "lineage"])
    E["m"] = E["d"].dt.to_period("M")
    E["q"] = E.groupby("m")["react"].transform(lambda x: pd.qcut(x.rank(method="first"), 5, labels=False) if len(x) >= 5 else np.nan)
    top = E[E["q"] == 4].assign(net=lambda x: x["excess"] - x["cost"])
    monthly = top.groupby("m").agg(net_excess=("net", "mean"), n=("net", "size"))
    monthly.index = monthly.index.to_timestamp()
    bottom = E[E["q"] == 0].assign(net=lambda x: x["excess"] - x["cost"]).groupby("m")["net"].mean()
    bottom.index = bottom.index.to_timestamp()
    return monthly, bottom, E


# ------------------------------------------------------------------------------------------------- position

def monthly_panel(P: dict):
    C = P["C"]
    ME = month_ends(C.index, "2015-01-01") + [C.index[-1]]
    Cm = C.loc[ME]
    Rm = Cm.pct_change(fill_method=None)
    Um = P["U"].loc[ME]
    mkt = Rm.where(Um.shift(1).fillna(False).astype(bool)).mean(axis=1)
    return ME, Cm, Rm, Um, mkt


def momentum_scores(P: dict) -> pd.DataFrame:
    """12-1 momentum at each month end (t-12 to t-1 months)."""
    ME, Cm, Rm, Um, mkt = monthly_panel(P)
    return Cm.shift(1) / Cm.shift(12) - 1


def residual_scores(P: dict, window: int = 36) -> pd.DataFrame:
    """P1: sum of market residuals over months t-12..t-2 (the 12-1 window) / residual sd over the trailing window."""
    ME, Cm, Rm, Um, mkt = monthly_panel(P)
    out = pd.DataFrame(np.nan, index=Rm.index, columns=Rm.columns)
    m = mkt.to_numpy()
    R = Rm.to_numpy()
    for i in range(window, len(Rm)):
        y = R[i - window + 1:i + 1]                          # months up to and including t (known at t's close)
        x = m[i - window + 1:i + 1]
        ok = np.isfinite(y)
        for j in np.flatnonzero(ok.sum(0) >= 24):
            mk = ok[:, j] & np.isfinite(x)
            xx, yy = x[mk], y[mk, j]
            b = np.cov(xx, yy)[0, 1] / np.var(xx, ddof=1)
            a = yy.mean() - b * xx.mean()
            e = y[:, j] - (a + b * x)
            sd = np.nanstd(e, ddof=1)
            mom = np.nansum(e[-12:-1]) if np.isfinite(e[-12:-1]).sum() >= 9 else np.nan
            out.iat[i, j] = mom / sd if sd > 0 else np.nan
    return out


def info_discreteness(P: dict) -> pd.DataFrame:
    """P3: sign(12-1 return) x (share of down days - share of up days) over the 12-1 window, at month ends."""
    C = P["C"]
    r = C.pct_change(fill_method=None)
    up = (r > 0).astype(float).where(r.notna())
    dn = (r < 0).astype(float).where(r.notna())
    pu = up.shift(21).rolling(231, min_periods=150).mean()
    pd_ = dn.shift(21).rolling(231, min_periods=150).mean()
    ME = month_ends(C.index, "2015-01-01") + [C.index[-1]]
    pret = (C.shift(21) / C.shift(252) - 1).loc[ME]
    return np.sign(pret) * (pd_.loc[ME] - pu.loc[ME])


def top_picks(P: dict, scores: pd.DataFrame, n: int = 20, mask: pd.DataFrame | None = None) -> dict:
    out = {}
    for d in scores.index:
        if d < pd.Timestamp("2016-01-01") or d == P["C"].index[-1]:
            continue
        uni = P["U"].loc[d]
        s = scores.loc[d, uni[uni].index].dropna()
        if mask is not None:
            s = s[mask.loc[d, s.index].fillna(False).astype(bool)]
        out[d] = list(s.nlargest(n).index)
    return out


def monthly_selection(P: dict, picks: dict) -> pd.DataFrame:
    """Hold each month-end's picks to the next month end (about 21 sessions)."""
    C = P["C"]
    ds = sorted(picks)
    rows, prev = [], pd.Series(dtype=float)
    U, cost = P["U"], P["cost"]
    for d, nxt in zip(ds, ds[1:] + [None]):
        if nxt is None:
            break
        r = C.loc[nxt] / C.loc[d] - 1
        missing = [x for x in picks[d] if x not in r.index or not np.isfinite(r[x])]
        if missing:
            raise ValueError(f"unresolved future return for selected holding(s) on {d}: {missing[:5]}")
        w = _weights(picks[d])
        uni = U.loc[d][U.loc[d]].index
        ub = float(r[uni].mean())
        g = float((w * r[w.index]).sum())
        dw = w.sub(prev, fill_value=0).abs()
        c = float((dw * cost.loc[d].reindex(dw.index).fillna(ROUND_TRIP["mid"]) / 2).sum())
        rows.append({"d": d, "gross": g, "bench": ub, "cost": c, "net": g - c, "net_excess": g - c - ub})
        prev = w * (1 + r[w.index].fillna(0))
        prev = prev / prev.sum()
    return pd.DataFrame(rows).set_index("d")


def vol_managed(net: pd.Series, target: float = 0.12, cap: float = 1.5, lookback: int = 6,
                rebalance_cost: float = 0.0015) -> pd.DataFrame:
    """P2: scale a monthly total-return series by target / trailing realised vol (known before the month)."""
    vol = net.rolling(lookback).std(ddof=1).shift(1) * np.sqrt(12)
    w = (target / vol).clip(upper=cap)
    rf = RF / 12
    m = w * net + (1 - w) * rf - w.diff().abs().fillna(0) * rebalance_cost
    return pd.DataFrame({"managed": m, "w": w, "raw": net}).dropna()


# ------------------------------------------------------------------------------------------------- F&O

def oi_buildup(rcon, P: dict, fut, k: int = 5) -> tuple[pd.DataFrame, pd.DataFrame]:
    """F1 long build-up (price up, OI change top quintile): long; F2 short covering (price up, OI change bottom
    quintile): short. Forward spot returns relative to the F&O names' mean, stock-futures costs."""
    fd = fut.execute("SELECT symbol, d, oi FROM futures_daily WHERE oi > 0").df()
    fd["d"] = pd.to_datetime(fd["d"])
    OI = fd.pivot(index="d", columns="symbol", values="oi").sort_index()
    tick = rcon.execute("SELECT DISTINCT ticker, lineage FROM adjusted_prices WHERE business_date >= '2016-01-01'").df()
    t2l = dict(zip(tick["ticker"], tick["lineage"]))
    cols = [s for s in OI.columns if s in t2l and t2l[s] in P["C"].columns]
    OI = OI[cols]
    S = P["C"][[t2l[s] for s in cols]]
    S.columns = cols
    OI = OI.reindex(S.index)
    R5 = S / S.shift(k) - 1
    D5 = OI / OI.shift(k) - 1
    F = fwd(S, k)
    long_rows, short_rows = [], []
    for d in every(S.index, k):
        ok = R5.loc[d].notna() & D5.loc[d].notna() & F.loc[d].notna()
        if ok.sum() < 50:
            continue
        r, oi, f = R5.loc[d, ok], D5.loc[d, ok], F.loc[d, ok]
        mean = f.mean()
        hi, lo = oi.quantile(0.8), oi.quantile(0.2)
        lb = f[(r > 0) & (oi >= hi)]
        sc = f[(r > 0) & (oi <= lo)]
        if len(lb):
            long_rows.append({"d": d, "net": float(lb.mean() - mean - STOCK_FUT_RT), "n": len(lb)})
        if len(sc):
            short_rows.append({"d": d, "net": float(-(sc.mean() - mean) - STOCK_FUT_RT), "n": len(sc)})
    return pd.DataFrame(long_rows).set_index("d"), pd.DataFrame(short_rows).set_index("d")


# ------------------------------------------------------------------------------------------------- index calendar

def nifty_tr(rcon) -> pd.Series:
    df = rcon.execute("""SELECT business_date, close_level FROM index_levels_canonical WHERE index_name = 'Nifty 50'
                         AND variant = 'TR' AND business_date >= '2015-06-01' ORDER BY 1""").df()
    if df.empty:
        df = rcon.execute("""SELECT business_date, close_level FROM index_levels_canonical WHERE index_name = 'Nifty 50'
                             AND variant = 'PRICE' AND business_date >= '2015-06-01' ORDER BY 1""").df()
    s = df.set_index("business_date")["close_level"]
    s.index = pd.to_datetime(s.index)
    return s


def turn_of_month(tr: pd.Series, before: int = 2, after: int = 3) -> pd.DataFrame:
    """Z1: long from the close ``before`` sessions before month end to the close of the ``after``-th session of the
    next month; net over cash."""
    idx = tr.index
    per = idx.to_period("M")
    rows = []
    months = sorted(set(per))
    for m in months[:-1]:
        in_m = idx[per == m]
        nxt = idx[per == m + 1]
        if len(in_m) <= before or len(nxt) < after:
            continue
        a, b = in_m[-1 - before], nxt[after - 1]
        days = (b - a).days
        rows.append({"d": a, "net": float(tr[b] / tr[a] - 1 - RF * days / 365 - INDEX_RT)})
    return pd.DataFrame(rows).set_index("d")


def pre_holiday(tr: pd.Series) -> pd.DataFrame:
    """Z2: long from the close before an exchange holiday (a skipped weekday) to the next close."""
    idx = tr.index
    rows = []
    for a, b in zip(idx[:-1], idx[1:]):
        gap = pd.bdate_range(a + pd.Timedelta(days=1), b - pd.Timedelta(days=1))
        if len(gap):
            rows.append({"d": a, "net": float(tr[b] / tr[a] - 1 - RF * (b - a).days / 365 - INDEX_RT)})
    return pd.DataFrame(rows).set_index("d")


# ------------------------------------------------------------------------------------------------- intraday

def half_hour_returns(rcon) -> pd.DataFrame:
    """Each name's return in each half-hour slot (09:15-09:45 ... 15:15-15:29), from minute closes."""
    return rcon.execute("""
        WITH b AS (SELECT tradingsymbol AS s, ts, close, CAST(ts AS DATE) AS d,
                          FLOOR((EXTRACT(HOUR FROM ts) * 60 + EXTRACT(MINUTE FROM ts) - 555) / 30) AS slot
                   FROM minute_bars WHERE tradingsymbol <> 'NIFTYBEES'),
             f AS (SELECT s, d, slot, ARG_MIN(close, ts) AS c0, ARG_MAX(close, ts) AS c1, COUNT(*) AS n FROM b
                   WHERE slot BETWEEN 0 AND 12 GROUP BY 1, 2, 3),
             p AS (SELECT *, LAG(c1) OVER (PARTITION BY s, d ORDER BY slot) AS prev_c1 FROM f)
        SELECT s, d, slot, COALESCE(prev_c1, c0) AS start_px, c1 AS end_px, n FROM p""").df()


def intraday_seasonality(hh: pd.DataFrame, lookback: int = 20) -> pd.DataFrame:
    """I2: per slot, long the top decile and short the bottom decile of the name's mean same-slot return over the
    previous ``lookback`` sessions; held for that half hour. Per day: the sum over slots, net of both legs' costs."""
    hh = hh[hh["n"] >= 10].copy()
    hh["r"] = hh["end_px"] / hh["start_px"] - 1
    hh["d"] = pd.to_datetime(hh["d"])
    out = []
    for slot, g in hh.groupby("slot"):
        R = g.pivot(index="d", columns="s", values="r").sort_index()
        sig = R.rolling(lookback, min_periods=15).mean().shift(1)
        for d in R.index:
            s, r = sig.loc[d].dropna(), R.loc[d]
            s = s[r.reindex(s.index).notna()]
            if len(s) < 100:
                continue
            hi, lo = s.quantile(0.9), s.quantile(0.1)
            spread = r[s[s >= hi].index].mean() - r[s[s <= lo].index].mean()
            out.append({"d": d, "slot": slot, "gross": spread, "net": spread - 2 * INTRADAY_RT})
    df = pd.DataFrame(out)
    return df.groupby("d").agg(gross=("gross", "sum"), net=("net", "sum"), slots=("slot", "size"))


def index_intraday_momentum(bars: pd.DataFrame, prev_close: pd.Series) -> pd.DataFrame:
    """I1: ``bars``: one index's minute bars (ts, close). Direction = sign(prev close -> 09:45); position 15:00 -> 15:29."""
    bars = bars.copy()
    bars["d"] = pd.to_datetime(bars["ts"]).dt.tz_localize(None).dt.normalize()
    bars["hm"] = pd.to_datetime(bars["ts"]).dt.strftime("%H:%M")
    rows = []
    for d, g in bars.groupby("d"):
        g = g.set_index("hm")["close"]
        pc = prev_close.get(d)
        if pc is None or not all(k in g.index for k in ("09:45", "15:00")):
            continue
        last = g[g.index <= "15:29"].iloc[-1]
        first = g["09:45"] / pc - 1
        lastr = last / g["15:00"] - 1
        if first == 0:
            continue
        rows.append({"d": d, "first": first, "last": lastr, "gross": np.sign(first) * lastr,
                     "net": np.sign(first) * lastr - INDEX_RT})
    return pd.DataFrame(rows).set_index("d")


# ------------------------------------------------------------------------------------------------- batch two

FUT_ONE_WAY = 0.0005
INDEX_ONE_WAY = 0.0002


def fno_panel(rcon, P: dict, fut) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Spot closes of the F&O names (sessions x symbols) and their membership (futures traded that session)."""
    fd = fut.execute("SELECT symbol, d, oi FROM futures_daily WHERE oi > 0").df()
    fd["d"] = pd.to_datetime(fd["d"])
    M = fd.pivot(index="d", columns="symbol", values="oi").notna()
    tick = rcon.execute("SELECT DISTINCT ticker, lineage FROM adjusted_prices WHERE business_date >= '2016-01-01'").df()
    t2l = dict(zip(tick["ticker"], tick["lineage"]))
    cols = [s for s in M.columns if s in t2l and t2l[s] in P["C"].columns and s not in
            ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50")]
    S = P["C"][[t2l[s] for s in cols]].copy()
    S.columns = cols
    M = M[cols].reindex(S.index).fillna(False).astype(bool)
    return S, M


def long_short(S: pd.DataFrame, M: pd.DataFrame, scores: pd.DataFrame, dates: list, *, frac: float = 0.10,
               one_way: float = FUT_ONE_WAY) -> pd.DataFrame:
    """Market-neutral: +1 spread over the top ``frac`` of ``scores``, -1 over the bottom, held to the next date;
    cost ``one_way`` per unit of weight changed (both legs)."""
    rows, prev = [], pd.Series(dtype=float)
    for d, nxt in zip(dates, dates[1:]):
        ok = M.loc[d] & scores.loc[d].notna() & S.loc[d].notna() & S.loc[nxt].notna()
        sc = scores.loc[d, ok[ok].index]
        if len(sc) < 50:
            continue
        hi, lo = sc.quantile(1 - frac), sc.quantile(frac)
        L, Sh = sc[sc >= hi].index, sc[sc <= lo].index
        w = pd.concat([pd.Series(1.0 / len(L), index=L), pd.Series(-1.0 / len(Sh), index=Sh)]).groupby(level=0).sum()
        r = S.loc[nxt, w.index] / S.loc[d, w.index] - 1
        g = float((w * r).sum())
        c = float(w.sub(prev, fill_value=0).abs().sum() * one_way)
        rows.append({"d": d, "gross": g, "cost": c, "net": g - c})
        prev = w
    return pd.DataFrame(rows).set_index("d")


def w_scores(S: pd.DataFrame) -> dict[str, pd.DataFrame]:
    return {"W1": -(S / S.shift(5) - 1),
            "W2": S / S.rolling(252, min_periods=200).max(),
            "W3": S.shift(21) / S.shift(252) - 1}


def ensemble_scores(P: dict) -> pd.DataFrame:
    """E1: mean cross-sectional rank of 12-1 momentum, residual momentum and nearness to the 52-week high,
    masked to the continuous-information half (NaN elsewhere)."""
    mom = momentum_scores(P)
    res = residual_scores(P)
    C = P["C"]
    ME = list(mom.index)
    near = (C / C.rolling(252, min_periods=200).max()).loc[ME]
    idd = info_discreteness(P)
    out = pd.DataFrame(np.nan, index=mom.index, columns=mom.columns)
    for d in ME:
        uni = P["U"].loc[d][P["U"].loc[d]].index
        parts = [x.loc[d, uni].rank(pct=True) for x in (mom, res, near)]
        avg = pd.concat(parts, axis=1).mean(axis=1, skipna=False)
        low = idd.loc[d, uni] <= idd.loc[d, uni].median()
        out.loc[d, uni] = avg.where(low)
    return out


def index_daily_from_minutes(bars: pd.DataFrame) -> pd.DataFrame:
    b = bars.copy()
    b["ts"] = pd.to_datetime(b["ts"])
    b["d"] = b["ts"].dt.tz_localize(None).dt.normalize()
    b["hm"] = b["ts"].dt.strftime("%H:%M")
    g = b.groupby("d")
    out = pd.DataFrame({"open": g["open"].first(), "close": g["close"].last()})
    p930 = b[b["hm"] == "09:29"].set_index("d")["close"]
    out["p930"] = p930
    out["prev_close"] = out["close"].shift(1)
    return out


def overnight(daily: pd.DataFrame) -> pd.Series:
    """X1: long close -> next open, 4 bp a day."""
    return (daily["open"] / daily["prev_close"] - 1 - 2 * INDEX_ONE_WAY).dropna()


def opening_fade(daily: pd.DataFrame, k: float = 2.0, window: int = 60) -> pd.DataFrame:
    """X2: fade a 09:15-09:30 move beyond ``k`` trailing standard deviations, 09:30 -> 15:29."""
    r15 = daily["p930"] / daily["open"] - 1
    sd = r15.rolling(window, min_periods=40).std().shift(1)
    sig = r15.abs() > k * sd
    rest = daily["close"] / daily["p930"] - 1
    ev = pd.DataFrame({"r15": r15, "rest": rest})[sig.fillna(False)]
    ev["gross"] = -np.sign(ev["r15"]) * ev["rest"]
    ev["net"] = ev["gross"] - 2 * INDEX_ONE_WAY
    return ev
