"""Testing the deep feature library honestly: every feature alone under a false-discovery-rate control, the fair
value gap claim as an event study, and combinations learned walk-forward - all as pre-registered in
``PRE-REGISTERED deep feature library and learned combinations``.

The panel is one row per (sampled session, name): the library's features, ``adv20`` for the universe and tiers, and
the forward returns ``fwd5`` / ``fwd20`` as outcomes only.
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

TIERS = (("large", 1, 50), ("next", 51, 100), ("mid", 101, 250), ("small", 251, 500))
SPLIT = date(2022, 1, 1)
ROUND_TRIP = 0.003
GBM_PARAMS = dict(n_estimators=500, learning_rate=0.03, num_leaves=31, min_child_samples=200,
                  colsample_bytree=0.7, subsample=0.7, subsample_freq=1, verbose=-1)


def bh(pvals: np.ndarray, q: float = 0.05) -> np.ndarray:
    """Benjamini-Hochberg: which hypotheses survive a false-discovery rate of ``q``."""
    p = np.asarray(pvals, dtype=float)
    n = len(p)
    order = np.argsort(p)
    thresh = q * (np.arange(1, n + 1) / n)
    passed = p[order] <= thresh
    k = np.max(np.flatnonzero(passed)) + 1 if passed.any() else 0
    out = np.zeros(n, dtype=bool)
    out[order[:k]] = True
    return out


def _t(x: pd.Series) -> float:
    x = x.dropna()
    return float(x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))) if len(x) > 2 and x.std(ddof=1) > 0 else float("nan")


def universe(panel: pd.DataFrame, names: int = 500) -> pd.DataFrame:
    p = panel[panel["adv20"] > 0].copy()
    p["liq_rank"] = p.groupby("d")["adv20"].rank(ascending=False, method="first")
    return p[p["liq_rank"] <= names]


def sweep(panel: pd.DataFrame, features: list[str], *, step20: int = 4) -> pd.DataFrame:
    """Per feature and horizon: IC, t, tiers, held-out t, long-only top-quintile net. ``step20``: use every
    ``step20``-th sampled date at h=20 so windows do not overlap (sampled every 5 sessions -> every 20th)."""
    from scipy.stats import norm
    dates = sorted(panel["d"].unique())
    keep20 = set(dates[::step20])
    rows = []
    for f in features:
        for h, ycol in ((5, "fwd5"), (20, "fwd20")):
            p = panel if h == 5 else panel[panel["d"].isin(keep20)]
            p = p[[f, ycol, "d", "liq_rank"]].dropna()
            if p[f].nunique() < 2 or len(p) < 1000:
                continue
            g = p.groupby("d")
            ic = g.apply(lambda x: x[f].rank().corr(x[ycol].rank()) if x[f].nunique() > 1 else np.nan).dropna()
            if len(ic) < 20:
                continue
            t = _t(ic)
            sign = np.sign(ic.mean()) or 1.0
            tiers = {}
            for name, lo, hi in TIERS:
                q = p[(p["liq_rank"] >= lo) & (p["liq_rank"] <= hi)]
                ti = q.groupby("d").apply(lambda x: x[f].rank().corr(x[ycol].rank()) if x[f].nunique() > 1 else np.nan)
                tiers[name] = _t(ti)
            agree = sum(1 for v in tiers.values() if v == v and np.sign(v) == sign and abs(v) >= 2)
            held = ic[[d >= SPLIT for d in ic.index]]
            # long-only: the top quintile in the feature's own direction, against the date's mean
            def top_excess(x):
                s = sign * x[f]
                k = max(1, int(0.2 * len(x)))
                return x.loc[s.nlargest(k).index, ycol].mean() - x[ycol].mean()
            ex = g.apply(top_excess)
            rows.append({"feature": f, "h": h, "dates": len(ic), "ic": float(ic.mean()), "t": t,
                         "p": float(2 * norm.sf(abs(t))) if t == t else 1.0, "tiers_agree": agree,
                         "held_out_t": _t(held), "top_excess": float(ex.mean()),
                         **{f"t_{k}": v for k, v in tiers.items()}})
    out = pd.DataFrame(rows)
    if len(out):
        out["fdr"] = bh(out["p"].to_numpy())
        out["counts"] = out["fdr"] & (out["tiers_agree"] >= 3) & (np.sign(out["held_out_t"]) == np.sign(out["ic"])) \
            & (out["held_out_t"].abs() >= 2)
    return out


def fvg_study(events: pd.DataFrame, daily_excess: pd.DataFrame) -> dict:
    """``events``: fvg_events rows with a ``lineage``. ``daily_excess``: (lineage, d) -> ex5, ex20 (forward return
    minus that day's universe mean). One observation per day for the retest returns."""
    out = {}
    for side in ("bull", "bear"):
        e = events[events["side"] == side]
        out[side] = {"gaps": int(len(e)), "filled_5": float(e["filled_5"].mean()), "filled_20": float(e["filled_20"].mean())}
        r = e.dropna(subset=["retest_d"]).merge(daily_excess, left_on=["lineage", "retest_d"], right_on=["lineage", "d"], how="inner")
        sgn = 1 if side == "bull" else -1
        for h in ("ex5", "ex20"):
            per_day = (sgn * r[h]).groupby(r["retest_d"]).mean()
            out[side][f"retest_{h}"] = float(per_day.mean()) if len(per_day) else float("nan")
            out[side][f"retest_{h}_t"] = _t(per_day)
        out[side]["retests"] = int(len(r))
    return out


def walk_forward(panel: pd.DataFrame, features: list[str], *, years=range(2019, 2027), embargo_days: int = 30,
                 model_factory=None, fill_nan: bool = False) -> pd.DataFrame:
    """Rank-transform features per date, train on everything ending ``embargo_days`` before each test year, predict
    that year. Returns the panel's test rows with a ``pred`` column."""
    if model_factory is None:
        import lightgbm as lgb
        model_factory = lambda: lgb.LGBMRegressor(**GBM_PARAMS)        # noqa: E731
    p = panel.copy()
    X = p.groupby("d")[features].rank(pct=True)
    if fill_nan:                                        # learners that cannot take a gap get the middle rank
        X = X.fillna(0.5)
    y = p.groupby("d")["fwd20"].rank(pct=True)
    p["_y"] = y
    preds = []
    for yr in years:
        start = date(yr, 1, 1)
        train = (p["d"] < start - timedelta(days=embargo_days)) & p["_y"].notna()
        test = (p["d"] >= start) & (p["d"] < date(yr + 1, 1, 1))
        if train.sum() < 10_000 or test.sum() == 0:
            continue
        m = model_factory()
        m.fit(X[train].to_numpy(dtype=np.float32), p.loc[train, "_y"].to_numpy())
        preds.append(pd.Series(m.predict(X[test].to_numpy(dtype=np.float32)), index=p.index[test]))
    p["pred"] = pd.concat(preds) if preds else np.nan
    return p[p["pred"].notna()].drop(columns="_y")


def books(p: pd.DataFrame, scorers: dict, *, ns=(20, 50), step: int = 4, cost_col: str = "rt") -> pd.DataFrame:
    """Monthly (every ``step``-th sampled date) equal-weight top-n books by each scorer, 20-session forward excess over
    the universe, charged ``cost_col`` round trips on the names that enter."""
    dates = sorted(p["d"].unique())[::step]
    rows, prev = [], {}
    for d in dates:
        x = p[(p["d"] == d)].dropna(subset=["fwd20"])
        if len(x) < 100:
            continue
        univ = x["fwd20"].mean()
        for name, col in scorers.items():
            s = x.dropna(subset=[col]).sort_values(col, ascending=False)
            for n in ns:
                top = s.head(n)
                new = set(top["lineage"]) - prev.get((name, n), set())
                cost = top.loc[top["lineage"].isin(new), cost_col].sum() / n
                rows.append({"d": d, "book": name, "n": n, "gross": top["fwd20"].mean() - univ, "cost": cost,
                             "turnover": len(new) / n})
                prev[(name, n)] = set(top["lineage"])
    r = pd.DataFrame(rows)
    r["net"] = r["gross"] - r["cost"]
    return r
