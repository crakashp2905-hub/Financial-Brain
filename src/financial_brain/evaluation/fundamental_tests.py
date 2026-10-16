"""The two fundamentals trials of amendment 4, on the point-in-time results store (2019-2026).

* ``x3_cohorts`` - ``PRE-REGISTERED earnings surprise drift out of sample``: standardised unexpected earnings on
  first-reported profit; each month's filers among the 500 most liquid, long the top fifth from the close two sessions
  after the filing, held 40 sessions, net of delivery costs, excess over the equal-weighted 500. Only filings
  disseminated after 2024-12-31 count (the earlier ones were seen in-sample).
* ``mf_book`` - ``PRE-REGISTERED multi-factor quality value low-risk book``: monthly, among the 500 most liquid with
  four known quarters and positive TTM profit, the best fifth by equal-weighted ranks of TTM net margin, earnings
  stability, earnings yield and low 60-session volatility.

Point in time throughout: a statement is used only from its ``filed_at``, and each quarter's figure is the
**first-reported** one (consolidated where the company files it, else standalone).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import standard as S

X3_FROM = pd.Timestamp("2025-01-01")
X3_HOLD, X3_LAG = 40, 2


def statements(rcon) -> pd.DataFrame:
    """First-reported quarterly statements with the lineage they trade under, and shares from the same filing."""
    df = rcon.execute("""
        WITH fr AS (SELECT isin, period_end, basis, filed_at, revenue, pat, news_id,
                           ROW_NUMBER() OVER (PARTITION BY isin, period_end, basis ORDER BY filed_at) AS k
                    FROM financial_results WHERE pat IS NOT NULL AND revenue IS NOT NULL),
             first AS (SELECT * FROM fr WHERE k = 1),
             pick AS (SELECT *, ROW_NUMBER() OVER (PARTITION BY isin, period_end
                                                   ORDER BY CASE basis WHEN 'consolidated' THEN 0 ELSE 1 END) AS b
                      FROM first),
             cap AS (SELECT seq_number,
                            MAX(value) FILTER (WHERE concept = 'PaidUpValueOfEquityShareCapital') AS paid_up,
                            MAX(value) FILTER (WHERE concept = 'FaceValueOfEquityShareCapital') AS face
                     FROM xbrl_facts WHERE ctx = 'Q'
                       AND concept IN ('PaidUpValueOfEquityShareCapital', 'FaceValueOfEquityShareCapital')
                     GROUP BY seq_number)
        SELECT p.isin, p.period_end, p.basis, p.filed_at, p.revenue, p.pat, cap.paid_up, cap.face
        FROM pick p LEFT JOIN cap ON cap.seq_number = replace(p.news_id, 'NSE:', '') WHERE p.b = 1""").df()
    lin = rcon.execute("""SELECT isin, ANY_VALUE(lineage) AS lineage FROM adjusted_prices
                          WHERE isin IS NOT NULL GROUP BY isin""").df()
    df = df.merge(lin, on="isin", how="inner")
    df["period_end"] = pd.to_datetime(df["period_end"])
    df["filed_at"] = pd.to_datetime(df["filed_at"])
    df["shares"] = np.where((df["face"] > 0) & (df["paid_up"] > 0), df["paid_up"] / df["face"], np.nan)
    return df.sort_values(["lineage", "period_end"]).reset_index(drop=True)


def raw_closes(rcon, lineages: list[str], dates: list) -> pd.DataFrame:
    """Unadjusted NSE closes (dates x lineage) for market capitalisation."""
    d = rcon.execute("""SELECT p.lineage, e.business_date AS d, e.close_price AS c FROM eod_prices e
                        JOIN (SELECT DISTINCT isin, lineage FROM adjusted_prices WHERE isin IS NOT NULL) p USING (isin)
                        WHERE e.exchange = 'NSE' AND e.series = 'EQ' AND p.lineage IN (SELECT UNNEST(?))
                          AND e.business_date IN (SELECT UNNEST(?))""", [lineages, [x.date() for x in dates]]).df()
    d["d"] = pd.to_datetime(d["d"])
    return d.pivot_table(index="d", columns="lineage", values="c", aggfunc="last")


# ------------------------------------------------------------------ X3: earnings surprise, out of sample

def sue_table(st: pd.DataFrame) -> pd.DataFrame:
    """SUE per first-reported statement: (PAT - PAT four quarters earlier) / sd of that change over the previous
    eight quarters (at least six), on the company's own chosen basis series."""
    out = []
    for lin, g in st.groupby("lineage"):
        g = g.set_index(g["period_end"].dt.to_period("Q")).sort_index()
        g = g[~g.index.duplicated(keep="first")]
        q = g.index
        pat = g["pat"]
        lag4 = pat.reindex(q - 4).to_numpy()
        diff = pd.Series(pat.to_numpy() - lag4, index=q)
        for i, per in enumerate(q):
            if not np.isfinite(diff.iloc[i]):
                continue
            hist = diff.reindex(pd.period_range(per - 8, per - 1, freq="Q")).dropna()
            if len(hist) < 6 or hist.std(ddof=1) <= 0:
                continue
            out.append({"lineage": lin, "period": per, "filed_at": g["filed_at"].iloc[i],
                        "sue": float(diff.iloc[i] / hist.std(ddof=1))})
    return pd.DataFrame(out)


def x3_cohorts(P: dict, sue: pd.DataFrame, *, draws: int = S.NULL_DRAWS, seed: int = 20261015) -> dict:
    C, U, cost = P["C"], P["U"], P["cost"]
    idx = C.index
    ew = C.pct_change(fill_method=None).where(U).mean(axis=1)
    cum = (1 + ew.fillna(0)).cumprod()
    ev = sue[sue["filed_at"] >= X3_FROM].copy()
    # the first session whose close comes after the filing (filings after 15:30 belong to the next session)
    t = ev["filed_at"]
    after_close = t.dt.hour * 60 + t.dt.minute >= 15 * 60 + 30
    s0 = idx.searchsorted(t.dt.normalize()) + after_close.astype(int).to_numpy()
    ev["entry"] = s0 + X3_LAG
    ev = ev[(ev["entry"] + X3_HOLD) < len(idx)]
    rows = []
    for r in ev.itertuples():
        e, x = idx[r.entry], idx[r.entry + X3_HOLD]
        if r.lineage not in C.columns or not U.at[e, r.lineage]:
            continue
        c0, c1 = C.at[e, r.lineage], C.at[x, r.lineage]
        if not (np.isfinite(c0) and np.isfinite(c1)):
            continue
        net = c1 / c0 - 1 - (cum[x] / cum[e] - 1) - cost.at[e, r.lineage]
        rows.append({"month": r.filed_at.to_period("M"), "sue": r.sue, "net": float(net)})
    D = pd.DataFrame(rows)
    picks = []
    for m, g in D.groupby("month"):
        if len(g) < 10:
            continue
        top = g[g["sue"] >= g["sue"].quantile(0.8)]
        picks.append({"month": m, "n": len(top), "net": float(top["net"].mean()), "pool": g["net"].to_numpy()})
    coh = pd.DataFrame(picks)
    real_t = S.tstat(coh["net"]) if len(coh) else np.nan
    rng = np.random.default_rng(seed)
    sims = []
    for _ in range(draws):
        vals = [float(rng.choice(p, size=n, replace=False).mean()) for p, n in zip(coh["pool"], coh["n"])]
        sims.append(S.tstat(pd.Series(vals)))
    q95 = float(np.nanquantile(sims, 0.95)) if sims else np.nan
    return {"cohorts": len(coh), "mean": float(coh["net"].mean()) if len(coh) else np.nan, "t": real_t,
            "null_q95": q95, "pass": bool(len(coh) >= 12 and coh["net"].mean() > 0 and real_t >= 2 and real_t > q95),
            "series": coh[["month", "n", "net"]].assign(month=lambda x: x["month"].astype(str)).to_dict("records")}


# ------------------------------------------------------------------ the multi-factor book

def mf_signals(P: dict, st: pd.DataFrame, closes_raw: pd.DataFrame, dates: list) -> dict[pd.Timestamp, pd.DataFrame]:
    """Per month end: eligible names (four known quarters, positive TTM profit) with their four signals."""
    C, U = P["C"], P["U"]
    vol60 = C.pct_change(fill_method=None).rolling(60, min_periods=50).std()
    by = {lin: g.reset_index(drop=True) for lin, g in st.groupby("lineage")}
    out = {}
    for d in dates:
        uni = U.loc[d][U.loc[d]].index
        rows = []
        for lin in uni:
            g = by.get(lin)
            if g is None:
                continue
            k = g[g["filed_at"] <= d]
            if len(k) < 4:
                continue
            last4, last8 = k.tail(4), k.tail(8)
            if (last4["period_end"].iloc[-1] - last4["period_end"].iloc[0]).days > 300:   # four consecutive quarters
                continue
            ttm_pat, ttm_rev = last4["pat"].sum(), last4["revenue"].sum()
            if ttm_pat <= 0 or ttm_rev <= 0:
                continue
            sh = k["shares"].dropna()
            px = closes_raw.at[d, lin] if (d in closes_raw.index and lin in closes_raw.columns) else np.nan
            mcap = sh.iloc[-1] * px if len(sh) and np.isfinite(px) else np.nan
            rows.append({"lineage": lin, "margin": ttm_pat / ttm_rev,
                         "stability": float((last8["pat"] > 0).sum()) if len(last8) >= 4 else np.nan,
                         "ey": ttm_pat / mcap if mcap and mcap > 0 else np.nan, "lowvol": -vol60.at[d, lin]})
        f = pd.DataFrame(rows).set_index("lineage") if rows else pd.DataFrame()
        if len(f) >= 50:
            f = f.dropna()
            ranks = f.rank(pct=True)
            f["score"] = ranks.mean(axis=1)
            out[d] = f
    return out


def mf_book(P: dict, sig: dict) -> dict:
    C, cost = P["C"], P["cost"]
    dates = sorted(sig)
    books, eligible, bench = {}, {}, {}
    F = pd.DataFrame(np.nan, index=dates[:-1], columns=C.columns)
    for d, nxt in zip(dates, dates[1:]):
        f = sig[d]
        r = C.loc[nxt, f.index] / C.loc[d, f.index] - 1
        f = f[r.notna()]
        if len(f) < 50:
            continue
        books[d] = list(f[f["score"] >= f["score"].quantile(0.8)].index)
        eligible[d] = list(f.index)
        F.loc[d, f.index] = r[f.index]
        bench[d] = float(r[f.index].mean())
    F = F.loc[list(books)]
    bench = pd.Series(bench)
    net = S.book_series(F, cost.reindex(F.index), bench, books)
    parts = {}
    for col in ("margin", "stability", "ey", "lowvol"):
        top = {d: list(sig[d].loc[eligible[d]][col].pipe(lambda s: s[s >= s.quantile(0.8)]).index) for d in books}
        parts[col] = S.book_series(F, cost.reindex(F.index), bench, top)
    turn = [len(set(books[b]) - set(books[a])) / max(len(books[b]), 1) for a, b in zip(list(books), list(books)[1:])]
    return {"net": net, "books": books, "eligible": eligible, "F": F, "cost": cost.reindex(F.index), "bench": bench,
            "parts": parts, "turnover": float(np.mean(turn)) if turn else np.nan}
