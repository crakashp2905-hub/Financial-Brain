"""Market breadth, and empirical sectors where the archive has no sector field.

**Breadth** asks how many names are participating, which a cap-weighted index hides: an index near its
high with a third of names above their 200-day average is a narrow market, and narrow markets are the
ones that break. It is computed from the feature table as of a session, nothing else.

**Empirical sectors.** The archive carries no industry taxonomy (the exchange's own field is blank for
almost every name), so sector structure is *measured*: names are clustered by return correlation over
the trailing year with Ward hierarchical clustering on the standardised residual returns (market removed). The
clusters are what the market actually moved together as, which is what a risk model wants, and they are
not always what a taxonomy would say - a bank and a NBFC land together, a conglomerate may sit alone. A
cluster's regime is its own momentum and breadth, ranked against the others.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import fcluster, linkage


class BreadthError(ValueError):
    pass


def breadth(con, as_of) -> dict:
    r = con.execute("""
        SELECT COUNT(*), AVG(CASE WHEN above_ma200 THEN 1.0 ELSE 0.0 END),
               AVG(CASE WHEN ret_20d > 0 THEN 1.0 ELSE 0.0 END),
               AVG(CASE WHEN dist_52w_high > -0.02 THEN 1.0 ELSE 0.0 END),
               STDDEV_SAMP(ret_20d), MEDIAN(ret_20d)
        FROM features WHERE business_date = ? AND adv20 >= 1e7 AND ret_20d IS NOT NULL""",
                    [as_of]).fetchone()
    if not r or not r[0]:
        raise BreadthError(f"no liquid names with features on {as_of}")
    above, up, high = r[1], r[2], r[3]
    state = ("broad" if above > 0.6 and up > 0.55 else "narrow" if above < 0.4 or up < 0.4 else "mixed")
    return {"as_of": as_of, "names": int(r[0]), "pct_above_ma200": float(above),
            "pct_up_20d": float(up), "pct_near_52w_high": float(high),
            "dispersion_20d": float(r[4]), "median_ret_20d": float(r[5]), "state": state}


def cluster_returns(returns: pd.DataFrame, n_clusters: int = 12, *, min_obs: int = 120,
                    remove_market: bool = True) -> pd.Series:
    """Cluster id (1..n) for each column of a (days x names) return matrix.

    The market return is removed first. Left in, one factor dominates every pairwise correlation and
    average linkage returns one enormous cluster and a scatter of outliers (334 of 400 names in one
    group when this was first run on the live universe). Ward linkage on the standardised residual
    series then splits what is left into groups of comparable size.
    """
    r = returns.dropna(axis=1, thresh=min_obs).fillna(0.0)
    if r.shape[1] < n_clusters * 2:
        raise BreadthError(f"{r.shape[1]} names is too few for {n_clusters} clusters")
    x = r.sub(r.mean(axis=1), axis=0) if remove_market else r
    sd = x.std(ddof=1).replace(0.0, np.nan)
    z = ((x - x.mean()) / sd).dropna(axis=1)
    if z.shape[1] < n_clusters * 2:
        raise BreadthError("too many constant return series to cluster")
    link = linkage(z.to_numpy().T, method="ward")
    return pd.Series(fcluster(link, n_clusters, criterion="maxclust"), index=z.columns)


def sector_regimes(con, as_of, *, n_clusters: int = 12, names: int = 400, lookback: int = 250) -> dict:
    top = con.execute("""
        SELECT lineage FROM features WHERE business_date = ? AND adv20 >= 1e7
        ORDER BY adv20 DESC LIMIT ?""", [as_of, names]).df()["lineage"].tolist()
    px = con.execute("""
        SELECT lineage, business_date, close_adj FROM adjusted_prices
        WHERE close_adj > 0 AND business_date <= ? AND business_date > ? - INTERVAL 400 DAY
          AND lineage IN (SELECT UNNEST(?))""", [as_of, as_of, top]).df()
    wide = px.pivot_table(index="business_date", columns="lineage", values="close_adj").tail(lookback + 1)
    ids = cluster_returns(wide.pct_change().iloc[1:], n_clusters)
    f = con.execute("""
        SELECT lineage, mom_12_1, ret_20d, above_ma200 FROM features
        WHERE business_date = ? AND lineage IN (SELECT UNNEST(?))""", [as_of, list(ids.index)]).df()
    f["cluster"] = f["lineage"].map(ids)
    g = f.groupby("cluster").agg(names=("lineage", "count"), mom_12_1=("mom_12_1", "median"),
                                 ret_20d=("ret_20d", "median"),
                                 pct_above_ma200=("above_ma200", "mean")).reset_index()
    g["rank"] = g["mom_12_1"].rank(ascending=False)
    g["regime"] = np.where(g["rank"] <= n_clusters / 3, "leading",
                           np.where(g["rank"] > 2 * n_clusters / 3, "lagging", "middle"))
    return {"as_of": as_of, "clusters": g.sort_values("rank").to_dict("records"),
            "membership": ids.to_dict()}
