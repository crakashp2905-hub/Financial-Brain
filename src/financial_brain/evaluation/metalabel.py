"""Meta-labelling: learn *which trades of a fixed strategy to skip*, without fitting rules to noise.

The primary strategy decides side and entry; this layer only asks "take it or skip it", from the context the trade
was taken in (López de Prado, *Advances in Financial Machine Learning*, ch. 3). It is the disciplined form of "do not
repeat the losing trades", and it has three guards because a losing streak always looks like a pattern:

1. **Enough evidence before anything is fitted.** ``MIN_TRADES`` closed trades over ``MIN_DAYS`` sessions, or
   ``fit`` refuses. One trade is not evidence.
2. **The unit is a context group, one observation per day, against the rest of that day.** Trades on the same day
   share one market, so the statistic is the daily difference between the group and the day's other trades; a group
   is flagged only if that difference is negative under Benjamini-Hochberg FDR control across every group examined
   (q = 0.10), negative in both halves of the sample, and the group itself loses money.
3. **Out of sample or not at all.** The model (an L2 logistic regression on the context dummies) is judged by purged
   walk-forward: fit on the past, skip on the next block, with ``EMBARGO`` days dropped between. It is reported as
   a proposal - a skip rule becomes real only as a new pre-registered rule version (ADR-0004).

Nothing here changes a live rule.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

MIN_TRADES = 300
MIN_DAYS = 60
FDR_Q = 0.10
EMBARGO = 5
FOLDS = 5


class NotEnoughEvidence(ValueError):
    pass


def check_evidence(trades: pd.DataFrame) -> dict:
    n, days = len(trades), trades["day"].nunique() if len(trades) else 0
    return {"trades": n, "days": int(days), "ok": n >= MIN_TRADES and days >= MIN_DAYS,
            "need": f"{MIN_TRADES} trades over {MIN_DAYS} sessions"}


def bh(p: np.ndarray, q: float = FDR_Q) -> np.ndarray:
    """Benjamini-Hochberg: which hypotheses are rejected at FDR ``q``."""
    p = np.asarray(p, dtype=float)
    m = len(p)
    if not m:
        return np.zeros(0, dtype=bool)
    order = np.argsort(p)
    passed = p[order] <= q * (np.arange(1, m + 1) / m)
    k = np.flatnonzero(passed).max() + 1 if passed.any() else 0
    out = np.zeros(m, dtype=bool)
    out[order[:k]] = True
    return out


def losing_groups(trades: pd.DataFrame, contexts: list[str], *, outcome: str = "net_bp") -> pd.DataFrame:
    """Every (context, value) group, tested **against the rest of the same days' trades**: per day, the group's mean
    minus the mean of that day's other trades, one observation per day with both. A plain group mean would inherit
    every other context's effect (each ``minute`` group holds its share of a losing ``gap`` group and looks like a
    loser too); the within-day difference removes the day and the mix. Flagged: the difference is negative under
    BH FDR control, in both halves, and the group's own mean is negative (skipping a worse-but-profitable group
    loses money)."""
    t = trades.copy()
    t["day"] = pd.to_datetime(t["day"])
    mid = t["day"].sort_values().iloc[len(t) // 2]
    day_sum = t.groupby("day")[outcome].agg(["sum", "count"])
    rows = []
    for c in contexts:
        for v, g in t.groupby(c, dropna=True):
            gs = g.groupby("day")[outcome].agg(["sum", "count"])
            rest_n = day_sum.loc[gs.index, "count"] - gs["count"]
            ok = rest_n > 0
            if ok.sum() < 5:
                continue
            gm = gs["sum"][ok] / gs["count"][ok]
            rm = (day_sum.loc[gs.index[ok], "sum"] - gs["sum"][ok]) / rest_n[ok]
            diff = gm - rm
            tt = stats.ttest_1samp(diff, 0.0)
            first, second = diff[diff.index < mid], diff[diff.index >= mid]
            own = g.groupby("day")[outcome].mean()
            rows.append({"context": c, "value": v, "days": int(ok.sum()), "trades": len(g), "mean": float(own.mean()),
                         "diff": float(diff.mean()), "t": float(tt.statistic), "p": float(tt.pvalue),
                         "diff_first": float(first.mean()) if len(first) else np.nan,
                         "diff_second": float(second.mean()) if len(second) else np.nan})
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["fdr"] = bh(df["p"].to_numpy())
    df["flag"] = df["fdr"] & (df["diff"] < 0) & (df["diff_first"] < 0) & (df["diff_second"] < 0) & (df["mean"] < 0)
    return df.sort_values("p").reset_index(drop=True)


def _design(t: pd.DataFrame, contexts: list[str]) -> pd.DataFrame:
    return pd.get_dummies(t[contexts].astype(str), drop_first=False).astype(float)


def _logit(X: np.ndarray, y: np.ndarray, l2: float = 1.0, iters: int = 50) -> np.ndarray:
    """L2-penalised logistic regression by Newton's method (intercept unpenalised)."""
    X1 = np.column_stack([np.ones(len(X)), X])
    w = np.zeros(X1.shape[1])
    pen = np.full(X1.shape[1], l2)
    pen[0] = 0.0
    for _ in range(iters):
        p = 1 / (1 + np.exp(-X1 @ w))
        g = X1.T @ (p - y) + pen * w
        H = (X1 * (p * (1 - p))[:, None]).T @ X1 + np.diag(pen)
        step = np.linalg.solve(H, g)
        w -= step
        if np.abs(step).max() < 1e-8:
            break
    return w


def walk_forward(trades: pd.DataFrame, contexts: list[str], *, outcome: str = "net_bp",
                 skip_below: float = 0.5) -> dict:
    """Purged walk-forward: for each later fold, fit on every earlier day (minus ``EMBARGO`` days before the fold),
    skip the fold's trades the model gives a win probability below ``skip_below``, and compare the taken trades'
    mean outcome with all of the fold's trades."""
    ev = check_evidence(trades)
    if not ev["ok"]:
        raise NotEnoughEvidence(f"{ev['trades']} trades over {ev['days']} sessions; need {ev['need']}")
    t = trades.copy()
    t["day"] = pd.to_datetime(t["day"])
    t = t.sort_values("day").reset_index(drop=True)
    X = _design(t, contexts)
    y = (t[outcome] > 0).astype(float).to_numpy()
    days = np.array(sorted(t["day"].unique()))
    blocks = np.array_split(days, FOLDS + 1)
    folds = []
    for b in blocks[1:]:
        cut = b[0] - np.timedelta64(EMBARGO, "D")
        tr = (t["day"] < cut).to_numpy()
        te = t["day"].isin(b).to_numpy()
        if tr.sum() < 50 or te.sum() < 10:
            continue
        w = _logit(X.to_numpy()[tr], y[tr])
        p = 1 / (1 + np.exp(-(np.column_stack([np.ones(te.sum()), X.to_numpy()[te]]) @ w)))
        out = t.loc[te, outcome].to_numpy()
        take = p >= skip_below
        folds.append({"start": str(b[0])[:10], "trades": int(te.sum()), "taken": int(take.sum()),
                      "all_mean": float(out.mean()), "taken_mean": float(out[take].mean()) if take.any() else np.nan})
    f = pd.DataFrame(folds)
    if f.empty:
        return {"folds": [], "improvement": None}
    imp = (f["taken_mean"] - f["all_mean"]).dropna()
    t_imp = float(imp.mean() / (imp.std(ddof=1) / np.sqrt(len(imp)))) if len(imp) > 2 and imp.std() > 0 else np.nan
    return {"folds": folds, "improvement_mean": float(imp.mean()) if len(imp) else None, "improvement_t": t_imp,
            "verdict": "PROPOSE" if len(imp) >= 3 and (imp > 0).all() and t_imp >= 2 else "NO_EVIDENCE"}


def fit(trades: pd.DataFrame, contexts: list[str], *, outcome: str = "net_bp") -> dict:
    ev = check_evidence(trades)
    if not ev["ok"]:
        raise NotEnoughEvidence(f"{ev['trades']} trades over {ev['days']} sessions; need {ev['need']}")
    groups = losing_groups(trades, contexts, outcome=outcome)
    return {"evidence": ev, "groups_examined": int(len(groups)),
            "flagged": groups[groups["flag"]].to_dict("records") if len(groups) else [],
            "walk_forward": walk_forward(trades, contexts, outcome=outcome)}


INTRADAY_CONTEXTS = ["strategy", "bucket", "daily_up", "gap_bin", "or_bin", "minute_bin"]


def from_intraday(live_con, *, mode: str = "live") -> pd.DataFrame:
    """Closed intraday paper trades with their recorded context, binned into the groups the review examines."""
    t = live_con.execute("""SELECT day, strategy, bucket, daily_up, gap, or_width, entry_minute, net_pnl,
                                   qty * entry_px AS notional FROM trades WHERE mode = ? AND exit_ts IS NOT NULL""",
                         [mode]).df()
    if t.empty:
        return t
    t["net_bp"] = t["net_pnl"] / t["notional"] * 1e4
    t["gap_bin"] = pd.cut(t["gap"], [-np.inf, -0.01, 0.01, np.inf], labels=["gap_down", "flat", "gap_up"])
    t["or_bin"] = pd.qcut(t["or_width"].rank(method="first"), 3, labels=["narrow", "mid", "wide"])
    t["minute_bin"] = pd.cut(t["entry_minute"], [-1, 60, 180, 1000], labels=["first_hour", "midday", "late"])
    t["daily_up"] = t["daily_up"].map({True: "up", False: "down"})
    return t


def readiness(live_con) -> dict:
    t = from_intraday(live_con)
    ev = check_evidence(t) if len(t) else {"trades": 0, "days": 0, "ok": False, "need": f"{MIN_TRADES} trades over {MIN_DAYS} sessions"}
    out = {"evidence": ev}
    if ev["ok"]:
        out["fit"] = fit(t, INTRADAY_CONTEXTS)
    return out
