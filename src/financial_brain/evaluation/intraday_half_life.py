"""The daily signals' information at intraday horizons: 5 minutes to the close.

Pre-registered in ``PRE-REGISTERED intraday half-life``. The daily half-life study measured 1 to 120 sessions; this
measures the minutes. For each (name, session) with minute bars, the price at 09:30 and at fixed offsets after it,
and each daily signal **as of the previous session** (so nothing from the session being measured leaks in). The IC
is the cross-sectional rank correlation per session, averaged, with a t across sessions.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

OFFSETS = {"5m": 575, "15m": 585, "30m": 600, "60m": 630, "120m": 690, "close": 915}
START = 570                        #: 09:30, minutes since midnight IST
SIGNALS = (("mom_12_1", 1), ("dist_52w_high", 1), ("vol_60", -1), ("ret_20d", -1))


class IntradayHalfLifeError(ValueError):
    pass


def prices(con) -> pd.DataFrame:
    """Per (isin, session): the close of the 09:30 minute and of each offset minute."""
    cols = ",\n".join(f"ARG_MAX(close, m) FILTER (WHERE m <= {m}) AS p_{k}" for k, m in OFFSETS.items())
    return con.execute(f"""
        WITH b AS (SELECT isin, CAST(ts AT TIME ZONE 'Asia/Kolkata' AS DATE) AS d,
                          hour(ts AT TIME ZONE 'Asia/Kolkata') * 60 + minute(ts AT TIME ZONE 'Asia/Kolkata') AS m, close
                   FROM minute_bars WHERE isin IS NOT NULL)
        SELECT isin, d AS session, ARG_MAX(close, m) FILTER (WHERE m <= {START}) AS p_start,
               {cols}
        FROM b GROUP BY isin, d HAVING COUNT(*) >= 300""").df()


def signals_previous_session(con, sessions) -> pd.DataFrame:
    """Each signal per (isin, session), taken from the latest feature date strictly before the session."""
    f = con.execute("""
        SELECT l.isin, f.business_date, f.mom_12_1, f.dist_52w_high, f.vol_60, f.ret_20d
        FROM features f JOIN security_lineage l ON l.lineage = f.lineage
        WHERE f.business_date >= ?::DATE - INTERVAL 10 DAY AND f.business_date <= ?""",
                    [min(sessions), max(sessions)]).df()
    fd = np.array(sorted(f["business_date"].unique()), dtype="datetime64[D]")
    prev = {}
    for s in sessions:
        k = np.searchsorted(fd, np.datetime64(s, "D")) - 1
        if k >= 0:
            prev[s] = pd.Timestamp(fd[k]).date()
    f["business_date"] = pd.to_datetime(f["business_date"]).dt.date
    m = pd.DataFrame({"session": list(prev), "business_date": list(prev.values())})
    return m.merge(f, on="business_date").drop(columns="business_date")


def ic_table(px: pd.DataFrame, sig: pd.DataFrame) -> dict:
    d = px.merge(sig, on=["isin", "session"])
    if d["session"].nunique() < 30:
        raise IntradayHalfLifeError("fewer than 30 sessions with both prices and signals")
    from ..opportunity import engine as E
    out = {}
    for k in OFFSETS:
        d[f"r_{k}"] = d[f"p_{k}"] / d["p_start"] - 1.0
    for name, sign in list(SIGNALS) + [("composite", 1)]:
        res = {}
        for k in OFFSETS:
            ics = []
            for s, g in d.groupby("session"):
                g = g.dropna(subset=[f"r_{k}", "mom_12_1", "dist_52w_high", "vol_60", "ret_20d"])
                if len(g) < 30:
                    continue
                x = E.composite(g) if name == "composite" else sign * g[name]
                ics.append(spearmanr(x, g[f"r_{k}"]).statistic)
            a = np.array([v for v in ics if np.isfinite(v)])
            res[k] = {"ic": float(a.mean()), "t": float(a.mean() / (a.std(ddof=1) / np.sqrt(len(a)))), "sessions": int(len(a))}
        out[name] = res
    return out
