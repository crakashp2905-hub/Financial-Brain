"""Backtest the hundred registered candidates (``hundred_spec.py``) and grade each under ADR-0007.

    python scripts/research/hundred.py --smoke     mechanics only: how many names each candidate picks, no returns
    python scripts/research/hundred.py --run       the backtests -> docs/trials/dynamic/hundred/

Read-only on every database. Decisions use data available at the rebalance close; a filing published at or after
15:00 counts from the next session. A holding with no later price is scored at its last traded price.
"""
from __future__ import annotations

import argparse
import json
import sys
import warnings
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import hundred_spec as SPEC  # noqa: E402

from financial_brain.evaluation import dynamic_standard as D  # noqa: E402
from financial_brain.evaluation import edges as E  # noqa: E402
from financial_brain.evaluation import standard as S  # noqa: E402

warnings.filterwarnings("ignore")
OUT = Path("docs/trials/dynamic/hundred")
RF = 0.065
MID = E.ROUND_TRIP["mid"]
START = pd.Timestamp("2016-01-01")
SECTORS = ["NIFTY ENERGY", "NIFTY METAL", "NIFTY PHARMA", "NIFTY REALTY", "NIFTY BANK", "NIFTY COMMODITIES",
           "NIFTY INDIA CONSUMPTION", "NIFTY IT", "NIFTY PSU BANK", "NIFTY INFRASTRUCTURE", "NIFTY AUTO", "NIFTY FMCG",
           "NIFTY MEDIA"]


# ================================================================================================ data

class Data:
    def __init__(self):
        self.con = duckdb.connect("data/financial_brain.duckdb", read_only=True)
        P = E.load(self.con, n=1000)
        self.C, self.K, self.cost, self.A = P["C"], P["K"], P["cost"], P["A"]
        self.P = P
        self.idx = self.C.index
        self.cols = self.C.columns
        self.Cff = self.C.ffill()
        self.R = self.C.pct_change(fill_method=None)
        self.ME = E.month_ends(self.idx, "2015-01-01") + [self.idx[-1]]
        lin = list(self.cols)
        self.con.register("_lin", pd.DataFrame({"lineage": lin}))
        ap = self.con.execute("""SELECT business_date d, lineage, isin, ticker, close_raw, turnover
                                 FROM adjusted_prices JOIN _lin USING (lineage)""").df()
        ap["d"] = pd.to_datetime(ap["d"])
        self.T = ap.pivot(index="d", columns="lineage", values="turnover").reindex(index=self.idx, columns=self.cols)
        self.RAW = ap.pivot(index="d", columns="lineage", values="close_raw").reindex(index=self.idx, columns=self.cols)
        last = ap.sort_values("d").groupby("lineage").tail(1)
        self.isin2lin = dict(ap.drop_duplicates(["isin", "lineage"])[["isin", "lineage"]].values)
        self.tick2lin = dict(ap.sort_values("d").drop_duplicates(["ticker"], keep="last")[["ticker", "lineage"]].values)
        self.lin2tick = dict(last[["lineage", "ticker"]].values)
        im = self.con.execute("""SELECT isin, any_value(industry) industry FROM index_membership
                                 WHERE index_name = 'NIFTY 500' GROUP BY isin""").df()
        ind = {self.isin2lin[i]: k for i, k in im.values if i in self.isin2lin}
        self.industry = pd.Series(ind).reindex(self.cols)
        self._cache: dict = {}
        # common daily panels
        self.mom = self.C.shift(21) / self.C.shift(252) - 1
        self.r20 = self.C / self.C.shift(20) - 1
        self.vol60 = self.R.rolling(60, min_periods=40).std()
        self.hi52 = self.C / self.C.rolling(252, min_periods=200).max()
        self.t20 = self.T.rolling(20, min_periods=10).mean()
        self.t250 = self.T.rolling(250, min_periods=120).mean()

    # ---- helpers
    def U(self, lo=1, hi=500) -> pd.DataFrame:
        return (self.K >= lo) & (self.K <= hi)

    def pos(self, ts: pd.Series) -> np.ndarray:
        """Index position of the first session on which something published at ``ts`` is usable at the close."""
        ts = pd.to_datetime(ts)
        eff = ts.dt.normalize() + pd.to_timedelta((ts.dt.hour >= 15).astype(int), unit="D")
        return self.idx.searchsorted(eff.values, side="left")

    def flag(self, lineage, pos, L: int, minc: int = 1) -> pd.DataFrame:
        """True for ``L`` sessions from each event (at least ``minc`` events in the window)."""
        col = self.cols.get_indexer(pd.Index(lineage))
        pos = np.asarray(pos)
        ok = (col >= 0) & (pos < len(self.idx))
        m = np.zeros((len(self.idx), len(self.cols)), dtype=np.float32)
        np.add.at(m, (pos[ok], col[ok]), 1.0)
        cs = np.cumsum(m, axis=0)
        win = cs.copy()
        win[L:] -= cs[:-L]
        return pd.DataFrame(win >= minc, index=self.idx, columns=self.cols)

    def asof(self, lineage, pos, values, limit: int) -> pd.DataFrame:
        """Last value per name, carried ``limit`` sessions from the session it became usable."""
        df = pd.DataFrame({"l": np.asarray(lineage), "p": np.asarray(pos), "v": np.asarray(values, dtype=float)})
        df = df[(df["p"] < len(self.idx)) & df["l"].isin(self.cols) & np.isfinite(df["v"])]
        df = df.drop_duplicates(["l", "p"], keep="last")
        w = df.pivot(index="p", columns="l", values="v")
        w.index = self.idx[w.index]
        return w.reindex(index=self.idx, columns=self.cols).ffill(limit=limit)

    def cached(self, key, fn):
        if key not in self._cache:
            self._cache[key] = fn()
        return self._cache[key]

    # ---- source tables
    def events(self) -> pd.DataFrame:
        def load():
            types = sorted({t for r in SPEC.C if r[6] == "event" for t in r[7]["types"]} | {"RESULTS"})
            q = ",".join(f"'{t}'" for t in types)
            df = self.con.execute(f"""SELECT isin, event_type, coalesce(published_at, CAST(business_date AS TIMESTAMP)) ts
                                      FROM announcements WHERE event_type IN ({q}) AND isin IS NOT NULL
                                      AND business_date >= '2015-01-01'""").df()
            df["l"] = df["isin"].map(self.isin2lin)
            df = df.dropna(subset=["l"])
            df["p"] = self.pos(df["ts"])
            return df
        return self.cached("events", load)

    def fundamentals(self) -> pd.DataFrame:
        def load():
            df = self.con.execute("""SELECT isin, period_end, basis, filed_at, revenue, total_expenses, pat
                                     FROM financial_results WHERE checks_passed AND basis IN ('consolidated','standalone')
                                     AND pat IS NOT NULL""").df()
            df["period_end"] = pd.to_datetime(df["period_end"])
            df["filed_at"] = pd.to_datetime(df["filed_at"])
            df["pref"] = (df["basis"] == "consolidated").astype(int)
            # first report of each quarter; consolidated where the company files it at all
            has_cons = df.groupby("isin")["pref"].transform("max")
            df = df[df["pref"] == has_cons].sort_values("filed_at").drop_duplicates(["isin", "period_end"], keep="first")
            df = df.sort_values(["isin", "period_end"]).reset_index(drop=True)
            prev = df[["isin", "period_end", "pat", "revenue", "total_expenses"]].copy()
            prev["period_end"] = prev["period_end"] + pd.DateOffset(years=1)
            prev["period_end"] = prev["period_end"] + pd.offsets.MonthEnd(0)
            df["period_end"] = df["period_end"] + pd.offsets.MonthEnd(0)
            df = df.merge(prev, on=["isin", "period_end"], how="left", suffixes=("", "_y"))
            df["d_pat"] = df["pat"] - df["pat_y"]
            df["pat_yoy"] = (df["d_pat"] / df["pat_y"].abs()).where(df["pat_y"].abs() > 0).clip(-5, 5)
            df["rev_yoy"] = (df["revenue"] / df["revenue_y"] - 1).where(df["revenue_y"] > 0).clip(-1, 5)
            df["exp_yoy"] = (df["total_expenses"] / df["total_expenses_y"] - 1).where(df["total_expenses_y"] > 0).clip(-1, 5)
            df["oplev"] = df["rev_yoy"] - df["exp_yoy"]
            df["margin"] = (df["pat"] / df["revenue"]).where(df["revenue"] > 0).clip(-2, 2)
            df["margin_y"] = (df["pat_y"] / df["revenue_y"]).where(df["revenue_y"] > 0).clip(-2, 2)
            df["margin_delta"] = df["margin"] - df["margin_y"]
            g = df.groupby("isin")
            df["rev_accel"] = df["rev_yoy"] - g["rev_yoy"].shift(1)
            sd = g["d_pat"].transform(lambda x: x.shift(1).rolling(8, min_periods=4).std())
            df["sue"] = (df["d_pat"] / sd).where(sd > 0).clip(-10, 10)
            up = (df["d_pat"] > 0).astype(float).where(df["d_pat"].notna())
            df["consistent"] = (up.groupby(df["isin"]).transform(lambda x: x.rolling(4, min_periods=4).sum()) == 4).astype(float)
            df["ttm"] = g["pat"].transform(lambda x: x.rolling(4, min_periods=4).sum())
            df["beat"] = ((df["pat_yoy"] > 0.25) & (df["rev_yoy"] > 0.15)).astype(float)
            df["turnaround"] = ((df["pat"] > 0) & (df["pat_y"] < 0)).astype(float)
            df["ttm_loss"] = (df["ttm"] < 0).astype(float)
            lag = (df["filed_at"].dt.normalize() - df["period_end"]).dt.days
            df["late"] = (lag > 44).astype(float)
            df["early"] = (lag <= 20).astype(float)
            df["l"] = df["isin"].map(self.isin2lin)
            df = df.dropna(subset=["l"])
            df["p"] = self.pos(df["filed_at"])
            return df
        return self.cached("fund", load)

    def ownership(self) -> pd.DataFrame:
        def load():
            f = self.con.execute("""SELECT record_id, isin, period_end, known_at, promoter_pct FROM shareholding_filings
                                    WHERE isin IS NOT NULL AND known_at IS NOT NULL""").df()
            cats = {"institutionsforeign": "fii", "mutualfundsoruti": "mf", "insurancecompanies": "ins"}
            q = ",".join(f"'{c}'" for c in cats)
            x = self.con.execute(f"""SELECT record_id, category, value FROM shareholding_facts
                                     WHERE concept = 'ShareholdingAsAPercentageOfTotalNumberOfShares'
                                     AND category IN ({q})""").df()
            x = x.groupby(["record_id", "category"])["value"].max().unstack().rename(columns=cats)
            r = self.con.execute("""SELECT record_id, max(value) retail FROM shareholding_facts
                                    WHERE concept = 'NumberOfShareholders' AND category =
                                    'residentindividualshareholdersholdingnominalsharecapitaluptorstwolakh'
                                    GROUP BY record_id""").df().set_index("record_id")
            f = f.join(x, on="record_id").join(r, on="record_id")
            f["period_end"] = pd.to_datetime(f["period_end"])
            f = f.sort_values("known_at").drop_duplicates(["isin", "period_end"], keep="first")
            f = f.sort_values(["isin", "period_end"])
            for c in ("fii", "mf", "ins"):
                if f[c].max() <= 1.5:                                   # stored as a fraction
                    f[c] = f[c] * 100
            g = f.groupby("isin")
            gap = (f["period_end"] - g["period_end"].shift(1)).dt.days
            ok = gap.between(80, 100)
            for c, name in (("promoter_pct", "d_promoter"), ("fii", "d_fii"), ("mf", "d_mf"), ("ins", "d_ins")):
                f[name] = (f[c] - g[c].shift(1)).where(ok)
            f["d_inst"] = f[["d_fii", "d_mf", "d_ins"]].sum(axis=1, min_count=2)
            f["g_retail"] = (f["retail"] / g["retail"].shift(1) - 1).where(ok).clip(-1, 5)
            f["l"] = f["isin"].map(self.isin2lin)
            f = f.dropna(subset=["l"])
            f["p"] = self.pos(pd.to_datetime(f["known_at"]))
            return f
        return self.cached("own", load)

    def delivery(self) -> pd.DataFrame:
        def load():
            df = self.con.execute("""SELECT symbol, business_date d, deliv_pct FROM delivery
                                     WHERE series = 'EQ' AND business_date >= '2015-01-01' AND deliv_pct IS NOT NULL""").df()
            df["l"] = df["symbol"].map(self.tick2lin)
            df = df.dropna(subset=["l"]).drop_duplicates(["d", "l"], keep="last")
            df["d"] = pd.to_datetime(df["d"])
            return df.pivot(index="d", columns="l", values="deliv_pct").reindex(index=self.idx, columns=self.cols)
        return self.cached("deliv", load)

    def deals(self) -> pd.DataFrame:
        def load():
            df = self.con.execute("""SELECT kind, trade_date d, symbol, side, qty * price v FROM bulk_block_deals
                                     WHERE trade_date >= '2015-01-01'""").df()
            df["l"] = df["symbol"].map(self.tick2lin)
            df = df.dropna(subset=["l"])
            df["d"] = pd.to_datetime(df["d"])
            df["p"] = self.idx.searchsorted(df["d"].values, side="right")     # published after the close: next session
            return df
        return self.cached("deals", load)

    def fno(self) -> dict:
        def load():
            f = duckdb.connect("data/fo_history.duckdb", read_only=True)
            fut = f.execute("""SELECT trade_date d, symbol, arg_min(close, expiry) fut, sum(oi) oi FROM fo_bhav
                               WHERE instrument = 'FUTSTK' AND trade_date >= '2015-06-01' AND close > 0
                               GROUP BY 1, 2""").df()
            opt = f.execute("""SELECT trade_date d, symbol, sum(oi) FILTER (WHERE option_type = 'PE') pe,
                                      sum(oi) FILTER (WHERE option_type = 'CE') ce FROM fo_bhav
                               WHERE instrument = 'OPTSTK' AND trade_date >= '2015-06-01' GROUP BY 1, 2""").df()
            nf = f.execute("""SELECT trade_date d, arg_min(close, expiry) fut, min(expiry) expiry FROM fo_bhav
                              WHERE instrument = 'FUTIDX' AND symbol = 'NIFTY' AND close > 0 GROUP BY 1""").df()
            no = f.execute("""SELECT trade_date d, sum(oi) FILTER (WHERE option_type = 'PE') pe,
                                     sum(oi) FILTER (WHERE option_type = 'CE') ce FROM fo_bhav
                              WHERE instrument = 'OPTIDX' AND symbol = 'NIFTY' GROUP BY 1""").df()
            out = {}
            for df, cols in ((fut, ["fut", "oi"]), (opt, ["pe", "ce"])):
                df["l"] = df["symbol"].map(self.tick2lin)
                df = df.dropna(subset=["l"]).drop_duplicates(["d", "l"], keep="last")
                df["d"] = pd.to_datetime(df["d"])
                for c in cols:
                    out[c] = df.pivot(index="d", columns="l", values=c).reindex(index=self.idx, columns=self.cols)
            for name, df in (("nifty_fut", nf), ("nifty_opt", no)):
                df["d"] = pd.to_datetime(df["d"])
                out[name] = df.set_index("d").sort_index()
            return out
        return self.cached("fno", load)

    def tri(self) -> pd.DataFrame:
        def load():
            df = self.con.execute("SELECT index_name, business_date d, tri FROM index_total_return").df()
            df["d"] = pd.to_datetime(df["d"])
            return df.pivot(index="d", columns="index_name", values="tri").sort_index().reindex(self.idx).ffill()
        return self.cached("tri", load)

    def macro(self, series: str) -> pd.Series:
        """The series as known at each session (observation date plus its release lag; a month for monthly data)."""
        df = self.con.execute("SELECT obs_date, value, release_lag_days lag FROM macro_series WHERE series = ? ORDER BY 1",
                              [series]).df()
        df["obs_date"] = pd.to_datetime(df["obs_date"])
        monthly = df["obs_date"].diff().dt.days.median() > 20
        avail = df["obs_date"] + pd.to_timedelta(df["lag"].fillna(1) + (31 if monthly else 0), unit="D")
        s = pd.Series(df["value"].values, index=avail).sort_index()
        s = s[~s.index.duplicated(keep="last")]
        return s.reindex(s.index.union(self.idx)).ffill().reindex(self.idx)


# ================================================================================================ engines

def run_book(X: Data, picks: dict, dates: list, U: pd.DataFrame, *, min_names: int = 5) -> pd.DataFrame:
    """Net excess of the picks over the equal-weight universe ``U``, rebalance date to the next. A date with
    fewer than ``min_names`` holds the universe (zero excess) after paying to leave the previous book."""
    rows, prev = [], pd.Series(dtype=float)
    Cff, cost = X.Cff, X.cost
    for d, nxt in zip(dates[:-1], dates[1:]):
        r = Cff.loc[nxt] / Cff.loc[d] - 1
        uni = U.columns[U.loc[d].to_numpy()]
        ub = float(r[uni].mean())
        names = [x for x in picks.get(d, []) if np.isfinite(r.get(x, np.nan))]
        cd = cost.loc[d]
        if len(names) < min_names:
            c = float((prev * cd.reindex(prev.index).fillna(MID) / 2).sum()) if len(prev) else 0.0
            rows.append((d, 0.0, c, -c, 0, 0.0))
            prev = pd.Series(dtype=float)
            continue
        w = pd.Series(1.0 / len(names), index=names)
        g = float((w * r[names]).sum())
        dw = w.sub(prev, fill_value=0).abs()
        c = float((dw * cd.reindex(dw.index).fillna(MID) / 2).sum())
        rows.append((d, g - ub, c, g - c - ub, len(names), float(dw.sum() / 2)))
        prev = w * (1 + r[names])
        prev = prev / prev.sum()
    return pd.DataFrame(rows, columns=["d", "gross_excess", "cost", "net_excess", "n", "turnover"]).set_index("d")


def top(X: Data, score: pd.DataFrame, dates: list, U: pd.DataFrame, n: int = 20, mask: pd.DataFrame | None = None) -> dict:
    out = {}
    for d in dates[:-1]:
        if d not in score.index:
            continue
        s = score.loc[d].where(U.loc[d]).where(X.C.loc[d].notna())
        if mask is not None:
            s = s.where(mask.loc[d].fillna(False).astype(bool))
        s = s.dropna()
        s = s[np.isfinite(s)]
        out[d] = list(s.nlargest(n).index) if len(s) else []
    return out


def flagged(X: Data, flag: pd.DataFrame, dates: list, U: pd.DataFrame, cap: int = 40) -> dict:
    out = {}
    for d in dates[:-1]:
        f = flag.loc[d].fillna(False).astype(bool) & U.loc[d] & X.C.loc[d].notna()
        names = f.index[f.to_numpy()]
        if len(names) > cap:
            names = X.K.loc[d, names].nsmallest(cap).index
        out[d] = list(names)
    return out


def timing(x: pd.Series, w: pd.Series, *, rf: float, cost: float = 0.0004) -> pd.Series:
    """Timing alpha: the strategy against itself held at its own trailing average exposure."""
    df = pd.DataFrame({"x": x, "w": w}).dropna()
    wbar = df["w"].expanding().mean().shift(1)
    c = cost * df["w"].diff().abs().fillna(0)
    return ((df["w"] - wbar) * (df["x"] - rf) - c).dropna()


# ================================================================================================ builders

def dates_me(X: Data, start=START) -> list:
    return [d for d in X.ME if d >= start]


def b_xs(X: Data, key: str, p: dict):
    """-> (score at month ends or None, mask, universe, n, dates, picks or None)"""
    U, n, mask, dates, picks = X.U(), 20, None, dates_me(X), None
    ME = X.ME
    if key == "mom":
        sc = X.C.shift(p["skip"]) / X.C.shift(p["look"]) - 1
    elif key == "hi52":
        sc = X.hi52
    elif key == "mom_riskadj":
        sc = X.mom / X.R.rolling(250, min_periods=200).std()
    elif key == "mom_ma200":
        sc, mask = X.mom, X.C > X.C.rolling(200, min_periods=150).mean()
    elif key == "ind_mom":
        r6 = (X.C / X.C.shift(126) - 1)
        picks = {}
        for d in dates[:-1]:
            u = U.loc[d]
            g = r6.loc[d].where(u).groupby(X.industry).mean().dropna()
            cnt = u.groupby(X.industry).sum()
            g = g[cnt.reindex(g.index) >= 8]
            best = set(g.nlargest(3).index)
            s = X.mom.loc[d].where(u & X.industry.isin(best)).dropna()
            picks[d] = list(s.nlargest(20).index)
        sc = None
    elif key == "mom_state":
        t = X.tri()["NIFTY 500"]
        up = (t / t.shift(252) - 1) > 0
        picks = top(X, X.mom, dates, U)
        picks = {d: (v if bool(up.loc[d]) else []) for d, v in picks.items()}
        sc = None
    elif key == "fip":
        U = X.U(p["lo"], p["hi"])
        idd = E.info_discreteness(X.P)
        med = idd.where(U.reindex(idd.index)).median(axis=1)
        mask = idd.le(med, axis=0).reindex(X.idx)
        sc = X.mom
    elif key == "ind_leadlag":
        picks = {}
        big, small = X.U(1, 100), X.U(101, 500)
        for d in dates[:-1]:
            lead = X.r20.loc[d].where(big.loc[d]).groupby(X.industry).mean()
            cnt = big.loc[d].groupby(X.industry).sum()
            lead = lead[cnt.reindex(lead.index) >= 3].dropna()
            best = set(lead.nlargest(3).index)
            s = (-X.r20.loc[d]).where(small.loc[d] & X.industry.isin(best)).dropna()
            picks[d] = list(s.nlargest(20).index)
        sc = None
    elif key == "ind_results_spill":
        ev = X.events()
        ev = ev[(ev["event_type"] == "RESULTS") & (ev["p"] >= 1) & (ev["p"] < len(X.idx) - 1)].drop_duplicates(["l", "p"])
        col = X.cols.get_indexer(ev["l"])
        Cn = X.Cff.to_numpy()
        mk = np.nanmean(X.R.where(X.U()).to_numpy(), axis=1)
        react = Cn[ev["p"].to_numpy() + 1, col] / Cn[ev["p"].to_numpy() - 1, col] - 1
        react = react - (mk[ev["p"].to_numpy()] + mk[ev["p"].to_numpy() + 1])
        ev = ev.assign(react=react, known=ev["p"] + 1, ind=X.industry.reindex(ev["l"]).to_numpy()).dropna(subset=["react", "ind"])
        reported = X.flag(ev["l"], ev["p"], 20)
        picks = {}
        for d in dates[:-1]:
            i = X.idx.get_loc(d)
            w = ev[(ev["known"] <= i) & (ev["known"] > i - 20)]
            g = w.groupby("ind")["react"].agg(["mean", "size"])
            best = set(g[g["size"] >= 3]["mean"].nlargest(3).index)
            ok = U.loc[d] & X.industry.isin(best) & ~reported.loc[d]
            picks[d] = list(X.K.loc[d].where(ok).dropna().nsmallest(20).index)
        sc = None
    elif key == "rev_lowvol":
        lo = X.r20.where(U).rank(axis=1, pct=True) <= 1 / 3
        sc, mask = -(X.t20 / X.t250), lo
    elif key == "ind_rev5":
        r5 = X.C / X.C.shift(5) - 1
        dates = [d for d in X.idx[::5] if d >= START]
        sub = r5.loc[dates].where(U.loc[dates])
        rel = sub.sub(sub.T.groupby(X.industry).transform("mean").T)
        sc = -rel
    elif key == "rev1m":
        U = X.U(p["lo"], p["hi"])
        sc = -X.r20
    elif key == "hv_winners":
        mask = X.r20.where(U).rank(axis=1, pct=True) >= 2 / 3
        sc = X.t20 / X.t250
    elif key == "lowvol":
        U = X.U(p["lo"], p["hi"])
        sc = -X.vol60
    elif key == "vol_of_turnover":
        sc = -(X.T.rolling(60, min_periods=40).std() / X.T.rolling(60, min_periods=40).mean())
    elif key == "deliv_level":
        sc = X.delivery().rolling(20, min_periods=10).mean()
    elif key == "deliv_accum":
        dl = X.delivery()
        sc, mask = dl.rolling(20, min_periods=10).mean() / dl.rolling(250, min_periods=120).mean(), X.r20 > 0
    elif key == "bulk_net":
        dl = X.deals()
        dl = dl[dl["kind"] == "bulk"]
        col = X.cols.get_indexer(dl["l"])
        ok = dl["p"].to_numpy() < len(X.idx)
        m = np.zeros((len(X.idx), len(X.cols)))
        sgn = np.where(dl["side"].str.upper().str.startswith("B"), 1.0, -1.0)
        np.add.at(m, (dl["p"].to_numpy()[ok], col[ok]), (sgn * dl["v"].to_numpy())[ok])
        net = pd.DataFrame(m, index=X.idx, columns=X.cols).rolling(20).sum() / (X.A * 20)
        sc = p["sign"] * net
        mask = sc > 0
    elif key == "fund":
        f = X.fundamentals()
        sc = X.asof(f["l"], f["p"], f[p["m"]], 90)
        dates = dates_me(X, pd.Timestamp("2019-06-01"))
    elif key == "fund_consistent":
        f = X.fundamentals()
        mask = X.asof(f["l"], f["p"], f["consistent"], 90) == 1
        sc = X.mom
        dates = dates_me(X, pd.Timestamp("2019-06-01"))
    elif key == "own":
        o = X.ownership()
        sc = p["sign"] * X.asof(o["l"], o["p"], o[p["m"]], 70)
        mask = sc > 0
        dates = dates_me(X, pd.Timestamp("2020-06-01"))
    elif key == "fno":
        F = X.fno()
        has = F["fut"].notna()
        U = has
        n = p["n"]
        if p["m"] == "basis":
            sc = p["sign"] * (F["fut"] / X.RAW - 1)
        elif p["m"] == "pcr":
            sc = p["sign"] * (F["pe"] / F["ce"]).where(F["ce"] > 0)
        else:
            sc, mask = F["oi"] / F["oi"].shift(20) - 1, X.r20 > 0
    elif key == "vcp":
        a = X.R.abs()
        sc, mask = -(a.rolling(20, min_periods=15).mean() / a.rolling(250, min_periods=150).mean()), X.hi52 >= 0.90
    elif key == "macro_beta":
        m = X.macro(p["series"])
        Cm = X.Cff.loc[ME]
        Rm = Cm.pct_change(fill_method=None).where(X.C.loc[ME].notna())
        xm = m.loc[ME].pct_change()
        trend = np.sign(m.loc[ME] / m.loc[ME].shift(3) - 1)
        sc = pd.DataFrame(np.nan, index=ME, columns=X.cols)
        rn, xn = Rm.to_numpy(), xm.to_numpy()
        for i in range(36, len(ME)):
            y, x = rn[i - 35:i + 1], xn[i - 35:i + 1]
            okx = np.isfinite(x)
            if okx.sum() < 24:
                continue
            xc = x[okx] - x[okx].mean()
            yy = y[okx]
            cnt = np.isfinite(yy).sum(0)
            beta = np.nansum((yy - np.nanmean(yy, axis=0)) * xc[:, None], axis=0) / (xc ** 2).sum()
            sc.iloc[i] = np.where(cnt >= 24, beta, np.nan) * trend.iloc[i]
    elif key == "comp_sue_mom":
        f = X.fundamentals()
        sue = X.asof(f["l"], f["p"], f["sue"], 90)
        dates = dates_me(X, pd.Timestamp("2019-06-01"))
        sc = sue.loc[dates].where(U.loc[dates]).rank(axis=1, pct=True) + X.mom.loc[dates].where(U.loc[dates]).rank(axis=1, pct=True)
    elif key == "comp_own_mom":
        o = X.ownership()
        di = X.asof(o["l"], o["p"], o["d_inst"], 70)
        dates = dates_me(X, pd.Timestamp("2020-06-01"))
        sc = di.loc[dates].where(U.loc[dates]).rank(axis=1, pct=True) + X.mom.loc[dates].where(U.loc[dates]).rank(axis=1, pct=True)
    elif key == "comp_a":
        idd = E.info_discreteness(X.P).reindex(dates)
        parts = [X.mom.loc[dates], X.hi52.loc[dates], -idd, -X.vol60.loc[dates]]
        sc = sum(q.where(U.loc[dates]).rank(axis=1, pct=True) for q in parts)
    else:
        raise KeyError(key)
    return sc, mask, U, n, dates, picks


def b_flag(X: Data, key: str, p: dict):
    U, dates = X.U(), dates_me(X)
    if key == "near_low":
        f = X.C / X.C.rolling(252, min_periods=200).min() <= 1.05
    elif key == "low_price":
        f = X.RAW < 50
    elif key == "young":
        f = X.C.notna().cumsum() < 250
        f = f & (X.C.notna().cumsum() > 0) & (X.idx.to_series().values[:, None] > np.datetime64("2016-06-01"))
    elif key == "deliv_spike_down":
        dl = X.delivery()
        ev = (X.R < -0.03) & (dl > 1.5 * dl.rolling(60, min_periods=30).mean().shift(1))
        f = ev.rolling(20).max() > 0
    elif key == "climax":
        ev = (X.T > 5 * X.t250.shift(1)) & ((X.C / X.C.shift(60) - 1) > 0.5)
        f = ev.rolling(5).max() > 0
    elif key == "block_buy":
        dl = X.deals()
        dl = dl[(dl["kind"] == "block") & dl["side"].str.upper().str.startswith("B")]
        f = X.flag(dl["l"], dl["p"], 40)
    elif key == "fund_flag":
        fu = X.fundamentals()
        fu = fu[fu[p["m"]] == 1]
        f = X.flag(fu["l"], fu["p"], p["L"])
        dates = dates_me(X, pd.Timestamp("2019-06-01"))
    elif key == "own_flag":
        o = X.ownership()
        o = o[p["sign"] * o[p["m"]] >= p["thr"]]
        f = X.flag(o["l"], o["p"], 60)
        dates = dates_me(X, pd.Timestamp("2020-06-01"))
    elif key == "event":
        ev = X.events()
        ev = ev[ev["event_type"].isin(p["types"])]
        f = X.flag(ev["l"], ev["p"], p["L"], p.get("min", 1))
    elif key == "rating":
        df = X.con.execute("""SELECT r.isin, r.published_at ts, coalesce(nullif(pd.sign, 0), r.sign) sign
                              FROM rating_actions r LEFT JOIN rating_pdf_direction pd USING (news_id)
                              WHERE r.published_at IS NOT NULL""").df()
        df = df[df["sign"] == p["sign"]]
        df["l"] = df["isin"].map(X.isin2lin)
        df = df.dropna(subset=["l"])
        f = X.flag(df["l"], X.pos(df["ts"]), p["L"])
    elif key == "fo_ban":
        df = X.con.execute("SELECT trade_date d, symbol FROM fo_ban WHERE trade_date >= '2015-01-01'").df()
        df["l"] = df["symbol"].map(X.tick2lin)
        df = df.dropna(subset=["l"])
        df["p"] = X.idx.searchsorted(pd.to_datetime(df["d"]).values, side="left")
        ban = X.flag(df["l"], df["p"], 1)
        entry = ban & ~ban.shift(1, fill_value=False)
        f = entry.rolling(p["L"]).max() > 0
    elif key == "new_top100":
        f = (X.K <= 100) & (X.K.shift(60) > 150)
    elif key == "reclaim200":
        above = (X.C > X.C.rolling(200, min_periods=150).mean()).astype(float)
        f = (above == 1) & (above.rolling(60).mean().shift(20) < 0.25)
    else:
        raise KeyError(key)
    return f, U, dates


def b_model(X: Data, key: str):
    """Walk-forward rank model on the feature library (a weekly grid of the 500 most liquid; the label is the
    20-session forward return). Refit each calendar year on dates at least 5 grid steps (25+ sessions) before the
    first test date, so no training label overlaps the test period; rebalanced on every fourth grid date."""
    pan = pd.read_parquet("data/feature_library_panel.parquet")
    pan["d"] = pd.to_datetime(pan["d"])
    feats = [c for c in pan.columns if c not in ("d", "fwd5", "fwd20", "lineage", "liq_rank", "__index_level_0__")]
    pan = pan[pan["lineage"].isin(X.cols)]
    grid = sorted(pan["d"].unique())
    pan["y"] = pan.groupby("d")["fwd20"].rank(pct=True)
    rk = pan.groupby("d")[feats].rank(pct=True) - 0.5
    picks = {}
    for year in range(2019, int(pd.Timestamp(grid[-1]).year) + 1):
        test = [d for d in grid if pd.Timestamp(d).year == year]
        if not test:
            continue
        train_dates = [d for d in grid if d < test[0]][:-5]                 # embargo: five weekly grid dates
        tr = pan["d"].isin(train_dates) & pan["y"].notna()
        te = pan["d"].isin(test)
        if key == "lgbm":
            import lightgbm as lgb
            mdl = lgb.LGBMRegressor(n_estimators=300, learning_rate=0.03, num_leaves=15, subsample=0.8,
                                    subsample_freq=1, colsample_bytree=0.5, min_child_samples=200,
                                    random_state=20261117, verbose=-1)
            mdl.fit(pan.loc[tr, feats], pan.loc[tr, "y"])
            pred = mdl.predict(pan.loc[te, feats])
        else:
            from sklearn.linear_model import Ridge
            mdl = Ridge(alpha=1000.0).fit(rk.loc[tr].fillna(0), pan.loc[tr, "y"])
            pred = mdl.predict(rk.loc[te].fillna(0))
        out = pan.loc[te, ["d", "lineage"]].assign(pred=pred)
        for d, g in out.groupby("d"):
            d = pd.Timestamp(d)
            if d not in X.idx:
                continue
            ok = X.U().loc[d].reindex(g["lineage"]).fillna(False).to_numpy()
            picks[d] = list(g[ok].nlargest(20, "pred")["lineage"])
    dates = sorted(picks)[::4]
    picks = {d: picks[d] for d in dates}
    return picks, dates + [X.idx[-1]]


def b_rot(X: Data, key: str, p: dict) -> pd.Series:
    t = X.tri()[SECTORS].loc[X.ME]
    r = t.pct_change().shift(-1)                                              # return over the month after each date
    if key == "rot_mom":
        sc = t.shift(p["skip"]) / t.shift(p["look"] + (1 if p["skip"] else 0)) - 1 if p["skip"] else t / t.shift(p["look"]) - 1
        sc = sc if p["top"] else -sc
    elif key == "rot_hi52":
        d = X.tri()[SECTORS]
        sc = (d / d.rolling(252).max()).loc[X.ME]
    elif key == "rot_lowvol":
        sc = -(X.tri()[SECTORS].pct_change().rolling(60).std()).loc[X.ME]
    elif key == "rot_mom_trend":
        sc = t.shift(1) / t.shift(7) - 1
    out, prev = {}, pd.Series(0.0, index=SECTORS)
    above = t > t.rolling(10).mean()
    for d in [x for x in X.ME[:-1] if x >= START]:
        s = sc.loc[d].dropna()
        if len(s) < 8:
            continue
        sel = list(s.nlargest(3).index)
        w = pd.Series(0.0, index=SECTORS)
        if key == "rot_mom_trend":
            sel = [x for x in sel if bool(above.loc[d, x])]
        w[sel] = 1 / 3
        ret = float((w * r.loc[d]).sum()) + (1 - w.sum()) * RF / 12
        c = 0.001 * float((w - prev).abs().sum())
        out[d] = ret - c - float(r.loc[d].mean())
        prev = w
    return pd.Series(out)


def b_timing(X: Data, key: str, p: dict) -> pd.Series:
    T = X.tri()
    ME = [d for d in X.ME if d >= pd.Timestamp("2015-06-01")]
    rf_m = RF / 12

    def monthly(index_name: str, w_daily: pd.Series) -> pd.Series:
        t = T[index_name].loc[ME]
        x = t.pct_change().shift(-1)
        s = timing(x, w_daily.reindex(ME).astype(float), rf=rf_m)
        return s[s.index >= START]

    if key == "t_ma10":
        t = T["NIFTY 50"].loc[ME]
        return monthly("NIFTY 50", (t > t.rolling(10).mean()).where(t.rolling(10).mean().notna()))
    if key == "t_breadth":
        b = X.con.execute("SELECT business_date d, above_200 FROM market_breadth").df()
        b = pd.Series(b["above_200"].values, index=pd.to_datetime(b["d"])).sort_index().reindex(X.idx).ffill()
        return monthly("NIFTY 500", (b > 0.5).where(b.notna()))
    if key == "t_volman":
        rv = T["NIFTY 500"].pct_change().rolling(20).std() * np.sqrt(252)
        return monthly("NIFTY 500", (0.12 / rv).clip(upper=1.0))
    reg = X.con.execute("SELECT business_date d, max(vix) vix FROM market_regime GROUP BY 1").df()
    vix = pd.Series(reg["vix"].values, index=pd.to_datetime(reg["d"])).sort_index().reindex(X.idx).ffill()
    if key == "t_vix":
        return monthly("NIFTY 50", (vix < 25).where(vix.notna()))
    if key == "t_vrp":
        rv = (T["NIFTY 50"].pct_change().rolling(20).std() * np.sqrt(252)) ** 2
        vrp = (vix / 100) ** 2 - rv
        return monthly("NIFTY 50", (vrp > vrp.expanding(min_periods=120).median()).where(vrp.notna()))
    if key == "t_part":
        df = X.con.execute("""SELECT trade_date d, measure, contracts FROM participant_positions WHERE kind = 'oi'
                              AND client_type = ? AND measure IN ('Future Index Long', 'Future Index Short')""",
                           [p["who"]]).df()
        w = df.pivot_table(index="d", columns="measure", values="contracts", aggfunc="last")
        w.index = pd.to_datetime(w.index)
        net = ((w["Future Index Long"] - w["Future Index Short"]) / (w["Future Index Long"] + w["Future Index Short"]))
        net = net.sort_index().reindex(X.idx).ffill().shift(1)              # published after the close
        chg = net - net.shift(20)
        sig = (chg < 0) if p["contra"] else (chg > 0)
        return monthly("NIFTY 50", sig.where(chg.notna()))
    if key == "t_pcr":
        o = X.fno()["nifty_opt"]
        pcr = (o["pe"] / o["ce"]).reindex(X.idx).ffill()
        return monthly("NIFTY 50", (pcr > pcr.rolling(250, min_periods=120).median()).where(pcr.notna()))
    if key == "t_basis":
        nf = X.fno()["nifty_fut"]
        spot = X.con.execute("""SELECT business_date d, close_level c FROM index_levels WHERE upper(index_name) = 'NIFTY 50'""").df()
        spot = pd.Series(spot["c"].values, index=pd.to_datetime(spot["d"])).sort_index()
        spot = spot[~spot.index.duplicated(keep="last")].reindex(X.idx)
        dte = pd.Series((pd.to_datetime(nf["expiry"]).values - nf.index.values) / np.timedelta64(1, "D"),
                        index=nf.index).clip(lower=1)
        basis = ((nf["fut"].reindex(X.idx) / spot - 1) * 365 / dte.reindex(X.idx)).ffill()
        return monthly("NIFTY 50", (basis < basis.rolling(250, min_periods=120).median()).where(basis.notna()))
    if key == "t_thrust":
        b = X.con.execute("SELECT business_date d, advances, declines FROM market_breadth").df()
        b.index = pd.to_datetime(b["d"])
        a = (b["advances"] / (b["advances"] + b["declines"])).sort_index().reindex(X.idx)
        thrust = a.rolling(10).mean() > 0.65
        return monthly("NIFTY 500", (thrust.rolling(20).max() > 0).where(a.rolling(10).mean().notna()))
    if key in ("pair_mom", "pair_macro"):
        a, b = T[p["a"]].loc[ME], T[p["b"]].loc[ME]
        x = (a.pct_change() - b.pct_change()).shift(-1)
        if key == "pair_mom":
            w = ((a / a.shift(6)) > (b / b.shift(6))).where(a.shift(6).notna())
        else:
            m = X.macro(p["series"]).loc[ME]
            ch = m / m.shift(3) - 1
            w = ((ch > 0) if p["up"] else (ch < 0)).where(ch.notna())
        s = timing(x, w.astype(float), rf=0.0, cost=0.002)
        return s[s.index >= START]
    if key == "t_tom":
        t = T["NIFTY MIDCAP 150"]
        x = t.pct_change().shift(-1)                                          # return from this close to the next
        per = X.idx.to_period("M")
        k_from_end = pd.Series(per, index=X.idx).groupby(per).cumcount(ascending=False)      # 0 = last session
        k_from_start = pd.Series(per, index=X.idx).groupby(per).cumcount()                   # 0 = first session
        # hold over the last 2 sessions' closes into the first 3 sessions: positions set at the close
        w = ((k_from_end <= 2) | (k_from_start <= 1)).astype(float)
        s = timing(x, w, rf=RF / 252)
        s = s[s.index >= START]
        return s.groupby(s.index.to_period("M")).sum().to_timestamp("M")
    raise KeyError(key)


# ================================================================================================ run

def build(X: Data, row) -> dict:
    cid, name, fam, prior, direction, engine, key, p = row
    if engine in ("xs", "xs5"):
        sc, mask, U, n, dates, picks = b_xs(X, key, p)
        if picks is None:
            picks = top(X, sc, dates, U, n, mask)
        return {"picks": picks, "dates": dates, "U": U}
    if engine == "flag":
        f, U, dates = b_flag(X, key, p)
        return {"picks": flagged(X, f, dates, U), "dates": dates, "U": U}
    if engine == "xs20":
        picks, dates = b_model(X, key)
        return {"picks": picks, "dates": dates, "U": X.U()}
    if engine == "rot":
        return {"series": b_rot(X, key, p)}
    return {"series": b_timing(X, key, p)}


def null_test(X: Data, b: dict, real_t: float, direction: int, draws: int = 100) -> dict:
    picks = {d: v for d, v in b["picks"].items() if d in b["dates"]}
    eligible = {d: list(b["U"].columns[(b["U"].loc[d] & X.C.loc[d].notna()).to_numpy()]) for d in picks}
    sims = []
    for i in range(draws):
        rb = S.random_books({d: v for d, v in picks.items() if len(v) >= 5}, eligible, seed=20261117 + i)
        full = {d: rb.get(d, []) for d in picks}
        sims.append(direction * S.tstat(run_book(X, full, b["dates"], b["U"])["net_excess"]))
    sims = np.array([s for s in sims if np.isfinite(s)])
    q = float(np.quantile(sims, 0.95))
    return {"null_q95": q, "null_mean": float(sims.mean()), "draws": int(len(sims)), "pass": bool(direction * real_t > q)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    X = Data()
    rows = [r for r in SPEC.C if not a.only or r[0] in a.only.split(",")]
    results, series = [], {}
    for row in rows:
        cid, name, fam, prior, direction, engine, key, p = row
        try:
            b = build(X, row)
        except Exception as e:                                               # an untestable candidate is recorded
            print(cid, "BUILD ERROR", type(e).__name__, str(e)[:160])
            results.append({"id": cid, "name": name, "tier": "UNTESTABLE", "error": f"{type(e).__name__}: {e}"[:300]})
            continue
        if a.smoke:
            if "picks" in b:
                cnt = [len(v) for d, v in b["picks"].items()]
                print(cid, engine, key, "dates", len(b["dates"]), "books", sum(1 for c in cnt if c >= 5),
                      "median n", int(np.median(cnt)) if cnt else 0, "first", str(min(b["picks"]))[:10] if cnt else "-")
            else:
                s = b["series"]
                print(cid, engine, key, "periods", len(s), "first", str(s.index.min())[:10], "nan", int(s.isna().sum()))
            continue
        if "picks" in b:
            bk = run_book(X, b["picks"], b["dates"], b["U"])
            active = bk[bk["n"] > 0]
            s = bk["net_excess"]
            extra = {"mean_n": float(active["n"].mean()) if len(active) else 0.0, "active_periods": int(len(active)),
                     "turnover": float(active["turnover"].mean()) if len(active) else 0.0,
                     "mean_cost": float(bk["cost"].mean()), "mean_gross_excess": float(bk["gross_excess"].mean())}
        else:
            s, extra = b["series"].dropna(), {}
        series[cid] = s
        if "picks" in b and extra["active_periods"] < 24:
            results.append({"id": cid, "name": name, "family": fam, "tier": "UNTESTABLE", **extra,
                            "error": f"only {extra['active_periods']} periods with 5 or more names"})
            print(cid, name[:58], "UNTESTABLE: thin", extra["active_periods"], flush=True)
            continue
        fam_n = SPEC.family_size(fam)
        g = D.judge(D.Spec(name, prior, fam_n, direction), s, null_pass=None)
        if "picks" in b and g.get("posterior", 0) >= 0.5 and g.get("both_halves"):
            nt = null_test(X, b, S.tstat(s), direction)
            extra["null"] = nt
            g = D.judge(D.Spec(name, prior, fam_n, direction), s, null_pass=nt["pass"])
        kind = "avoid" if direction < 0 else "book"
        if kind == "avoid":
            g["capital_fraction"] = 0.0
        res = {"id": cid, "family": fam, "engine": engine, "kind": kind, "mean": float(s.mean()), **g, **extra}
        results.append(res)
        print(cid, f"{name[:58]:58s}", fam, prior, fam_n, "n", g.get("n"), "mean %+.3f%%" % (100 * s.mean()),
              "t", g.get("t_used"), "halves", g.get("t_a"), g.get("t_b"), "P", g.get("posterior"), g.get("tier"), flush=True)
    if a.run:
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "results.json").write_text(json.dumps(results, indent=1, default=str), encoding="utf-8")
        long = pd.concat([s.rename("v").to_frame().assign(id=k) for k, s in series.items()])
        long.index.name = "d"
        long.reset_index().to_parquet(OUT / "series.parquet")
        tiers = pd.Series([r["tier"] for r in results]).value_counts().to_dict()
        print(tiers)


if __name__ == "__main__":
    main()
