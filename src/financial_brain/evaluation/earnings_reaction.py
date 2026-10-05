"""How the market reacts to results, by how surprising they were - from the exchange's own XBRL filings.

Pre-registered in ``PRE-REGISTERED setup analogues and earnings reaction``.

For every quarterly results filing in the point-in-time store: the standardised unexpected earnings **as computable
at the moment of filing** (this quarter's basic EPS minus the same quarter a year earlier, over the standard
deviation of the previous such changes), and the price response:

    react   previous close -> first close at or after dissemination (the part nobody could trade)
    d5, d20 that close -> 5 and 20 sessions later (the drift that a trader could still capture)

all minus the equal-weight mean of the liquid universe over the same window.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..features import fundamentals as FU

CLOSE_MINUTES = 15 * 60 + 30


class ReactionError(ValueError):
    pass


def sue_at_filing(fr: pd.DataFrame) -> pd.DataFrame:
    """One row per filing: its quarter's SUE using only quarters known at that filing (restatements included)."""
    out = []
    for key, g in fr.groupby("key"):
        g = g.sort_values("filed_at")
        for r in g.itertuples():
            q = FU.known_quarters(g, r.filed_at)
            if q.empty or q["period_end"].iloc[0] != r.period_end:
                continue                                  # a restatement of an older quarter, not news about the latest
            f = FU.factors_for(q, price=None, shares=None)
            if np.isfinite(f["sue"]):
                out.append({"key": key, "filed_at": r.filed_at, "period_end": r.period_end, "sue": f["sue"]})
    return pd.DataFrame(out)


def reactions(events: pd.DataFrame, wide: pd.DataFrame, *, horizons=(5, 20)) -> pd.DataFrame:
    """``events``: key, filed_at (timestamp). ``wide``: closes, index = date, columns = key."""
    dates = pd.to_datetime(wide.index)
    W = wide.to_numpy()
    mean_ret = lambda a, b: np.nanmean(W[b] / W[a] - 1.0)          # noqa: E731
    cols = {c: j for j, c in enumerate(wide.columns)}
    rows = []
    for r in events.itertuples():
        if r.key not in cols:
            continue
        t = pd.Timestamp(r.filed_at)
        after_close = t.hour * 60 + t.minute >= CLOSE_MINUTES
        i = int(np.searchsorted(dates.values, np.datetime64(t.normalize())))
        if i < len(dates) and dates[i] == t.normalize() and after_close:
            i += 1
        if i < 1 or i + max(horizons) >= len(dates):
            continue
        j = cols[r.key]
        if not (np.isfinite(W[i - 1, j]) and np.isfinite(W[i, j])):
            continue
        row = {"key": r.key, "filed_at": t, "sue": r.sue,
               "react": W[i, j] / W[i - 1, j] - 1.0 - mean_ret(i - 1, i)}
        for h in horizons:
            row[f"d{h}"] = (W[i + h, j] / W[i, j] - 1.0) - mean_ret(i, i + h) if np.isfinite(W[i + h, j]) else np.nan
        rows.append(row)
    return pd.DataFrame(rows)


def by_quintile(df: pd.DataFrame, cols=("react", "d5", "d20")) -> dict:
    if len(df) < 100:
        raise ReactionError(f"{len(df)} events is too few for quintiles")
    d = df.copy()
    d["q"] = pd.qcut(d["sue"].rank(method="first"), 5, labels=[1, 2, 3, 4, 5]).astype(int)
    tab = d.groupby("q")[list(cols)].mean()
    out = {"table": tab, "n": int(len(d))}
    for c in cols:
        a, b = d.loc[d["q"] == 5, c].dropna(), d.loc[d["q"] == 1, c].dropna()
        se = np.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b))
        out[f"{c}_spread"] = float(a.mean() - b.mean())
        out[f"{c}_t"] = float((a.mean() - b.mean()) / se) if se > 0 else float("nan")
    return out
