"""``PRE-REGISTERED published price anomalies``: the catalogue's 16 untested price anomalies, as registered.

Cross-sectional A1-A13: signals at month ends on the 500 most liquid names (``edges.load``); long the best fifth,
equal weight, held to the next month end, net of per-name delivery costs; measured as net excess over the
equal-weighted 500 (``standard.book_series``, the same function the null uses). Calendar A14-A15: Nifty timing alpha
at 15:15 (``index_rules``). A16: India's March year-end, one event a year.

``depth`` adds what the registration reports beside the verdict: the long-short gross spread, the monthly rank IC and
its t overall and in four size tiers, turnover and the net Sharpe.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import edges as E
from . import standard as S

M21 = 21
TIERS = {"1-50": (1, 50), "51-100": (51, 100), "101-250": (101, 250), "251-500": (251, 500)}

#: anomaly -> side (+1: high is good, -1: low is good)
SIDES = {"A1_intermediate_mom": +1, "A2_long_term_reversal": -1, "A3_return_seasonality": +1, "A4_ivol": -1,
         "A5_max": -1, "A6_idio_skew": -1, "A7_vol_of_vol": -1, "A8_downside_beta": +1, "A9_coskewness": -1,
         "A10_amihud": +1, "A11_volume_trend": -1, "A12_zero_days": +1, "A13_illiq_shock": +1}


def panel(rcon) -> dict:
    P = E.load(rcon)
    C = P["C"]
    v = rcon.execute("""SELECT lineage, business_date AS d, COALESCE(traded_volume, 0) AS v FROM adjusted_prices
                        WHERE lineage IN (SELECT UNNEST(?)) AND business_date >= '2015-01-01'""", [list(C.columns)]).df()
    v["d"] = pd.to_datetime(v["d"])
    V = v.pivot_table(index="d", columns="lineage", values="v").reindex(index=C.index, columns=C.columns)
    n = rcon.execute("""SELECT business_date, close_level FROM index_levels_canonical WHERE index_name = 'Nifty 50'
                        AND variant = 'PRICE' ORDER BY 1""").df()
    N = n.set_index(pd.to_datetime(n["business_date"]))["close_level"].reindex(C.index).ffill()
    P.update(V=V, N=N, R=C.pct_change(fill_method=None), RN=N.pct_change())
    return P


def month_ends(P: dict) -> list:
    return E.month_ends(P["C"].index, "2015-01-01") + [P["C"].index[-1]]


# ------------------------------------------------------------------ signals at one month end

def _window_stats(R: pd.DataFrame, RN: pd.Series, i: int, w: int) -> dict:
    """Market-model regressions over sessions (i-w, i]: beta, residual sd, residual skew, coskewness."""
    r = R.iloc[i - w + 1:i + 1].to_numpy()
    m = RN.iloc[i - w + 1:i + 1].to_numpy()
    ok_m = np.isfinite(m)
    r, m = r[ok_m], m[ok_m]
    valid = np.isfinite(r).sum(axis=0) >= int(0.8 * len(m))
    rr = np.where(np.isfinite(r), r, np.nan)
    mm = m - m.mean()
    rc = rr - np.nanmean(rr, axis=0)
    beta = np.nanmean(rc * mm[:, None], axis=0) / (mm ** 2).mean()
    e = rc - beta * mm[:, None]
    sd = np.nanstd(e, axis=0, ddof=1)
    skew = np.nanmean(e ** 3, axis=0) / sd ** 3
    cosk = np.nanmean(e * (mm ** 2)[:, None], axis=0) / (np.sqrt(np.nanmean(e ** 2, axis=0)) * (mm ** 2).mean())
    down = m < 0
    md = m[down] - m[down].mean()
    rd = rr[down] - np.nanmean(rr[down], axis=0)
    dbeta = np.nanmean(rd * md[:, None], axis=0) / (md ** 2).mean()
    nan = ~valid
    return {k: np.where(nan, np.nan, x) for k, x in
            {"beta": beta, "ivol": sd, "skew": skew, "cosk": cosk, "dbeta": dbeta}.items()}


def signals(P: dict, dates: list) -> dict[str, pd.DataFrame]:
    C, R, RN, V = P["C"], P["R"], P["RN"], P["V"]
    idx = {d: i for i, d in enumerate(C.index)}
    RV = (V * C).where(V > 0)                                   # rupee volume
    illiq = (R.abs() / RV).replace([np.inf, -np.inf], np.nan)
    rv20 = R.rolling(20, min_periods=15).std()
    out = {k: pd.DataFrame(np.nan, index=dates, columns=C.columns) for k in SIDES}
    # monthly closes for seasonality
    Cm = C.loc[dates]
    Rm = Cm.pct_change(fill_method=None)
    for d in dates:
        i = idx[d]
        if i >= 252:
            out["A1_intermediate_mom"].loc[d] = C.iloc[i - 7 * M21] / C.iloc[i - 12 * M21] - 1
            st = _window_stats(R, RN, i, 60)
            out["A4_ivol"].loc[d] = st["ivol"]
            out["A6_idio_skew"].loc[d] = st["skew"]
            st250 = _window_stats(R, RN, i, 250)
            out["A8_downside_beta"].loc[d] = st250["dbeta"]
            out["A9_coskewness"].loc[d] = st250["cosk"]
            out["A5_max"].loc[d] = R.iloc[i - 20:i + 1].max()
            rv = rv20.iloc[i - 249:i + 1]
            out["A7_vol_of_vol"].loc[d] = rv.std() / rv.mean()
            ilw = illiq.iloc[i - 249:i + 1]
            out["A10_amihud"].loc[d] = illiq.iloc[i - 59:i + 1].mean()
            out["A11_volume_trend"].loc[d] = RV.iloc[i - 59:i + 1].mean() / RV.iloc[i - 249:i + 1].mean()
            rz = R.iloc[i - 249:i + 1]
            out["A12_zero_days"].loc[d] = (rz == 0).sum() / rz.notna().sum()
            out["A13_illiq_shock"].loc[d] = illiq.iloc[i - 19:i + 1].mean() / ilw.iloc[:-20].mean()
        if i >= 36 * M21:
            out["A2_long_term_reversal"].loc[d] = C.iloc[i - 13 * M21] / C.iloc[i - 36 * M21] - 1
        # seasonality: the same calendar month in previous years (the month about to be held is d's next month)
        j = dates.index(d)
        lags = [j + 1 - 12 * y for y in range(1, 11) if j + 1 - 12 * y >= 1]
        if len(lags) >= 2:
            out["A3_return_seasonality"].loc[d] = Rm.iloc[lags].mean()
    for k in out:
        out[k] = out[k].replace([np.inf, -np.inf], np.nan)
    return out


# ------------------------------------------------------------------ books, outcome, null, depth

def books_for(P: dict, sig: pd.DataFrame, side: int, dates: list) -> tuple[dict, dict, pd.DataFrame, pd.Series]:
    C, U = P["C"], P["U"]
    books, eligible = {}, {}
    hold = [d for d in dates if d >= pd.Timestamp("2016-01-01")][:-1]
    F = pd.DataFrame(np.nan, index=hold, columns=C.columns)
    bench = {}
    for d, nxt in zip(hold, [x for x in dates if x > hold[0]]):
        uni = U.loc[d][U.loc[d]].index
        f = C.loc[nxt] / C.loc[d] - 1
        s = (side * sig.loc[d, uni]).dropna()
        s = s[f.reindex(s.index).notna()]
        F.loc[d] = f
        bench[d] = float(f[uni].mean())
        if len(s) < 100:
            continue
        books[d] = list(s[s >= s.quantile(0.8)].index)
        eligible[d] = list(s.index)
    return books, eligible, F.loc[list(books)], pd.Series(bench).reindex(list(books))


def run_cross(P: dict, sigs: dict, name: str, dates: list, *, side: int | None = None) -> dict:
    side = SIDES[name] if side is None else side
    books, eligible, F, bench = books_for(P, sigs[name], side, dates)
    cost = P["cost"].reindex(F.index)
    net = S.book_series(F, cost, bench, books)
    return {"net": net, "books": books, "eligible": eligible, "F": F, "cost": cost, "bench": bench}


def depth(P: dict, sig: pd.DataFrame, side: int, r: dict) -> dict:
    F, K = r["F"], P["K"]
    ics, tier_ics, spreads = [], {t: [] for t in TIERS}, []
    for d in F.index:
        s = (side * sig.loc[d]).reindex(r["eligible"][d])
        f = F.loc[d, s.index]
        ok = s.notna() & f.notna()
        if ok.sum() < 50:
            continue
        ics.append(s[ok].rank().corr(f[ok].rank()))
        top, bot = s[ok] >= s[ok].quantile(0.8), s[ok] <= s[ok].quantile(0.2)
        spreads.append(f[ok][top].mean() - f[ok][bot].mean())
        rk = K.loc[d, s[ok].index]
        for t, (lo, hi) in TIERS.items():
            m = (rk >= lo) & (rk <= hi)
            if m.sum() >= 20:
                tier_ics[t].append(s[ok][m].rank().corr(f[ok][m].rank()))

    def tt(x):
        x = np.array([v for v in x if np.isfinite(v)])
        return (float(x.mean()), float(x.mean() / (x.std(ddof=1) / np.sqrt(len(x))))) if len(x) > 2 else (np.nan, np.nan)
    turn = [len(set(r["books"][b]) - set(r["books"][a])) / max(len(r["books"][b]), 1)
            for a, b in zip(list(r["books"]), list(r["books"])[1:])]
    net = r["net"]
    return {"ic": tt(ics), "tiers": {t: tt(v) for t, v in tier_ics.items()}, "ls_gross": tt(spreads),
            "turnover": float(np.mean(turn)) if turn else np.nan,
            "sharpe_net": float(net.mean() / net.std() * np.sqrt(12)) if len(net) > 2 else np.nan}


# ------------------------------------------------------------------ A14-A16

def calendar_positions(p: pd.Series) -> dict[str, pd.Series]:
    nxt = pd.Series(p.index, index=p.index).shift(-1)
    a14 = pd.Series(np.where(nxt.dt.weekday == 0, 0.0, 1.0), index=p.index)      # flat over the session ending Monday
    a15 = pd.Series(np.where(nxt.dt.month.isin([11, 12, 1, 2, 3, 4]), 1.0, 0.0), index=p.index)
    return {"A14_day_of_week": a14, "A15_halloween": a15}


def year_end(P: dict) -> tuple[pd.DataFrame, callable]:
    """A16 per year: bottom fifth by the April-February return, bought at the close of the fifth-last March session,
    held to the April month end; excess over the equal-weighted 500 minus each name's round trip."""
    C, U, cost = P["C"], P["U"], P["cost"]
    idx = C.index
    rows, pools = [], {}
    for y in range(2016, idx[-1].year + 1):
        mar = idx[(idx.year == y) & (idx.month == 3)]
        apr = idx[(idx.year == y) & (idx.month == 4)]
        feb = idx[(idx.year == y) & (idx.month == 2)]
        prev_mar = idx[(idx.year == y - 1) & (idx.month == 3)]
        if len(mar) < 5 or not len(apr) or not len(feb) or not len(prev_mar):
            continue
        d0, d1 = mar[-5], apr[-1]
        uni = U.loc[d0][U.loc[d0]].index
        past = (C.loc[feb[-1], uni] / C.loc[prev_mar[-1], uni] - 1).dropna()
        fwd = (C.loc[d1, uni] / C.loc[d0, uni] - 1)
        net = fwd - fwd.mean() - cost.loc[d0, uni]
        net = net[past.index].dropna()
        losers = past[past <= past.quantile(0.2)].index.intersection(net.index)
        rows.append({"d": d0, "out": float(net[losers].mean()), "n": len(losers)})
        pools[d0] = net.to_numpy()
    ev = pd.DataFrame(rows)

    def pool(d, n, rng):
        arr = pools[d]
        return [float(rng.choice(arr, size=min(n, len(arr)), replace=False).mean())]
    return ev, pool


#: The out-of-sample books of ``PRE-REGISTERED high-volume return premium``: (signal, side), sides reversed.
HV = {"HV1_rising_volume": ("A11_volume_trend", +1), "HV2_improving_liquidity": ("A13_illiq_shock", -1)}


def targets(rcon, day) -> dict[str, dict]:
    """Every cross-sectional anomaly's book (and the two HV books) for ``day``'s close: the best fifth by the
    registered side, equal weight, among the 500 most liquid."""
    P = panel(rcon)
    P = {k: (v.loc[: pd.Timestamp(day)] if isinstance(v, (pd.DataFrame, pd.Series)) else v) for k, v in P.items()}
    d = P["C"].index[-1]
    dates = E.month_ends(P["C"].index, "2015-01-01") + [d]
    sigs = signals(P, dates)
    uni = P["U"].loc[d][P["U"].loc[d]].index
    out = {}
    books = {**{k: (k, s) for k, s in SIDES.items()}, **HV}
    for name, (sig_name, side) in books.items():
        s = (side * sigs[sig_name].loc[d, uni]).dropna()
        if len(s) < 100:
            out[name] = {}
            continue
        top = s[s >= s.quantile(0.8)].index
        out[name] = {k: 1.0 / len(top) for k in top}
    return out


# ------------------------------------------------------------------ 2027Q1: the held-out replication (not run before)

def held_out_panel(rcon) -> dict:
    """The panel for ``PRE-REGISTERED high-volume return premium`` / ``drying-up volume avoid filter`` test 2: the
    universe each month end is ranks **501-1000** by 20-session turnover - names the anomaly test never used."""
    P = E.load(rcon, n=1000)
    K = P["K"]
    P["U"] = (K > 500) & (K <= 1000)
    v = rcon.execute("""SELECT lineage, business_date AS d, COALESCE(traded_volume, 0) AS v FROM adjusted_prices
                        WHERE lineage IN (SELECT UNNEST(?)) AND business_date >= '2015-01-01'""",
                     [list(P["C"].columns)]).df()
    v["d"] = pd.to_datetime(v["d"])
    P["V"] = v.pivot_table(index="d", columns="lineage", values="v").reindex(index=P["C"].index, columns=P["C"].columns)
    n = rcon.execute("""SELECT business_date, close_level FROM index_levels_canonical WHERE index_name = 'Nifty 50'
                        AND variant = 'PRICE' ORDER BY 1""").df()
    P["N"] = n.set_index(pd.to_datetime(n["business_date"]))["close_level"].reindex(P["C"].index).ffill()
    P["R"], P["RN"] = P["C"].pct_change(fill_method=None), P["N"].pct_change()
    return P


def avoid_series(P: dict, sig: pd.DataFrame, side: int, dates: list) -> pd.Series:
    """The avoid reading: the flagged fifth's equal-weighted return minus the universe's, gross, per month end.
    ``side`` picks the flagged end: DV1 bottom fifth of volume trend (side -1), DV2 top fifth of illiquidity shock
    (side +1)."""
    C, U = P["C"], P["U"]
    out = {}
    hold = [d for d in dates if d >= pd.Timestamp("2016-01-01")]
    for d, nxt in zip(hold, hold[1:]):
        uni = U.loc[d][U.loc[d]].index
        f = (C.loc[nxt, uni] / C.loc[d, uni] - 1)
        s = (side * sig.loc[d, uni]).dropna()
        s = s[f.reindex(s.index).notna()]
        if len(s) < 100:
            continue
        flagged = s[s >= s.quantile(0.8)].index
        out[d] = float(f[flagged].mean() - f[s.index].mean())
    return pd.Series(out, dtype=float)
