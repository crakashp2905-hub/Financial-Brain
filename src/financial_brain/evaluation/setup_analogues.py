"""What happened the last 200 times a stock looked like this? Nearest-neighbour analogues over a ranked feature
space, and their outcomes beside the outcomes of everything.

Pre-registered in ``PRE-REGISTERED setup analogues and earnings reaction``.

Each past (name, date) situation is described by eight features, each **ranked within its own date** (so a 5%
move in a calm month and in a crash are compared as what they were - unusual or not - rather than as numbers):

    ret_1d, ret_5d, ret_20d, mom_12_1, dist_52w_high, vol_60, rsi_14, volume_surge (turnover / 20-day average)

The pool samples every ``STEP``-th session. Outcomes are excess returns over the cross-sectional mean at 1, 3, 5 and
20 sessions, plus the maximum favourable and adverse excursion over 5 sessions (raw, from daily closes). A query
uses only situations whose outcome windows had closed before the query date, and reports the analogues next to
the whole eligible pool, because a 60% win rate means nothing until you know the pool's is 55%.
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
from scipy.stats import norm

FEATURES = ("ret_1d", "ret_5d", "ret_20d", "mom_12_1", "dist_52w_high", "vol_60", "rsi_14", "volume_surge")
HORIZONS = (1, 3, 5, 20)
STEP = 5
K = 200


class AnalogueError(ValueError):
    pass


def rank_normal(df: pd.DataFrame, cols, by: str = "business_date") -> pd.DataFrame:
    out = df.copy()
    for c in cols:
        r = out.groupby(by)[c].rank(pct=True)
        n = out.groupby(by)[c].transform("count")
        out[c] = norm.ppf(((r * n) - 0.5) / n)
    return out


def build_pool(con, *, start: date, end: date, step: int = STEP, min_adv: float = 1e7) -> pd.DataFrame:
    days = [r[0] for r in con.execute(
        "SELECT DISTINCT business_date FROM features WHERE business_date BETWEEN ? AND ? ORDER BY 1", [start, end]).fetchall()]
    keep = set(days[::step])
    f = con.execute("""
        SELECT f.lineage, f.business_date, p.ticker, f.ret_1d, f.ret_5d, f.ret_20d, f.mom_12_1, f.dist_52w_high,
               f.vol_60, f.rsi_14, p.turnover / NULLIF(f.adv20, 0) AS volume_surge
        FROM features f JOIN adjusted_prices p ON p.lineage = f.lineage AND p.business_date = f.business_date
        WHERE f.business_date BETWEEN ? AND ? AND f.adv20 >= ?""", [start, end, min_adv]).df()
    f["business_date"] = pd.to_datetime(f["business_date"]).dt.date      # plain dates, comparable with a query date
    keep = {pd.Timestamp(d).date() for d in keep}
    f = f[f["business_date"].isin(keep)].dropna(subset=list(FEATURES))
    px = con.execute("SELECT lineage, business_date, close_adj FROM adjusted_prices WHERE close_adj > 0 AND business_date >= ?",
                     [start]).df()
    wide = px.pivot_table(index="business_date", columns="lineage", values="close_adj")
    pos = {pd.Timestamp(d).date(): i for i, d in enumerate(wide.index)}
    W = wide.to_numpy()
    cols = {c: j for j, c in enumerate(wide.columns)}
    i = f["business_date"].map(pos).to_numpy()
    j = f["lineage"].map(cols).to_numpy()
    ok = ~(pd.isna(i) | pd.isna(j))
    f, i, j = f[ok].copy(), i[ok].astype(int), j[ok].astype(int)
    T = len(wide)
    for h in HORIZONS:
        reach = np.minimum(i + h, T - 1)
        r = np.where(i + h < T, W[reach, j] / W[i, j] - 1.0, np.nan)
        f[f"r{h}"] = r
        f[f"x{h}"] = f[f"r{h}"] - f.groupby("business_date")[f"r{h}"].transform("mean")
    path = np.stack([W[np.minimum(i + k, T - 1), j] / W[i, j] - 1.0 for k in range(1, 6)], axis=1)
    path[i + 5 >= T] = np.nan
    f["mfe5"], f["mae5"] = np.nanmax(path, axis=1), np.nanmin(path, axis=1)
    return rank_normal(f, FEATURES).reset_index(drop=True)


def target_vector(cross_section: pd.DataFrame, ticker: str) -> np.ndarray:
    """The ticker's features ranked within its own date's cross-section."""
    cs = cross_section.dropna(subset=list(FEATURES)).copy()
    cs["business_date"] = 0
    z = rank_normal(cs, FEATURES)
    row = z[z["ticker"] == ticker]
    if row.empty:
        raise AnalogueError(f"{ticker} has no complete feature row on this date")
    return row[list(FEATURES)].to_numpy(dtype=float)[0]


def query(pool: pd.DataFrame, x: np.ndarray, as_of: date, *, k: int = K) -> dict:
    """The k nearest closed situations before ``as_of`` and their outcomes, against the eligible pool."""
    eligible = pool[pd.to_datetime(pool["business_date"]) <= pd.Timestamp(as_of - timedelta(days=45))]
    eligible = eligible.dropna(subset=[f"x{h}" for h in HORIZONS] + ["mfe5", "mae5"])
    if len(eligible) < 5 * k:
        raise AnalogueError(f"only {len(eligible)} closed situations before {as_of}")
    X = eligible[list(FEATURES)].to_numpy(dtype=float)
    d = np.sqrt(((X - x) ** 2).sum(axis=1))
    nn = eligible.iloc[np.argsort(d)[:k]]
    out = {"as_of": as_of, "k": int(len(nn)), "pool": int(len(eligible)),
           "median_distance": float(np.median(np.sort(d)[:k]))}
    for h in HORIZONS:
        a, p = nn[f"x{h}"], eligible[f"x{h}"]
        se = a.std(ddof=1) / np.sqrt(len(a))
        out[f"h{h}"] = {"mean": float(a.mean()), "median": float(a.median()), "win_rate": float((a > 0).mean()),
                        "pool_mean": float(p.mean()), "pool_win_rate": float((p > 0).mean()),
                        "edge": float(a.mean() - p.mean()), "edge_t": float((a.mean() - p.mean()) / se) if se > 0 else 0.0}
    out["mfe5_median"], out["mae5_median"] = float(nn["mfe5"].median()), float(nn["mae5"].median())
    best, worst = nn.loc[nn["x5"].idxmax()], nn.loc[nn["x5"].idxmin()]
    out["best"] = {"ticker": str(best["ticker"]), "date": str(best["business_date"]), "x5": float(best["x5"])}
    out["worst"] = {"ticker": str(worst["ticker"]), "date": str(worst["business_date"]), "x5": float(worst["x5"])}
    out["positive"], out["negative"] = int((nn["x5"] > 0).sum()), int((nn["x5"] <= 0).sum())
    out["pool_mfe5_median"], out["pool_mae5_median"] = float(eligible["mfe5"].median()), float(eligible["mae5"].median())
    return out
