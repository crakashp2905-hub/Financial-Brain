"""Avoid names whose results the market just rejected (``Result of bad news keeps going``, test N2).

A RESULTS announcement's reaction is the name's return from the close before it to the close of the session after
it, minus the equal-weighted 500 most liquid over the same sessions. A reaction in the bottom fifth of the
reactions of the previous 30 days' results (the live stand-in for "within its month") flags the name for the next
20 sessions: in names the test never saw, such names lagged by 1.3% (2016-21, t -2.6) and 2.1% (2022-26, t -4.7)
over those 20 sessions.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

HOLD = 20             #: sessions a flag lasts
WINDOW_DAYS = 30      #: reactions compared with the previous 30 days' results
QUANTILE = 0.20


def reactions(rcon, day: date, *, lookback_sessions: int = 70) -> pd.DataFrame:
    """Every RESULTS reaction whose window closed by ``day``'s close, over the last ``lookback_sessions``."""
    S = [pd.Timestamp(r[0]) for r in rcon.execute(
        """SELECT DISTINCT business_date FROM features WHERE business_date <= ? ORDER BY 1 DESC LIMIT ?""",
        [day, lookback_sessions + 3]).fetchall()][::-1]
    if len(S) < 5:
        return pd.DataFrame(columns=["lineage", "ann", "after", "react"])
    ev = rcon.execute("""SELECT DISTINCT a.business_date AS ann, p.lineage FROM announcements a
                         JOIN (SELECT DISTINCT isin, lineage FROM adjusted_prices WHERE isin IS NOT NULL) p USING (isin)
                         WHERE a.event_type = 'RESULTS' AND a.business_date > ? AND a.business_date <= ?""",
                      [S[0].date(), day]).df()
    if ev.empty:
        return pd.DataFrame(columns=["lineage", "ann", "after", "react"])
    ev["ann"] = pd.to_datetime(ev["ann"])
    idx = pd.DatetimeIndex(S)
    p0 = idx.searchsorted(ev["ann"])
    ev = ev.assign(p0=p0)
    ev = ev[(ev["p0"] >= 1) & (ev["p0"] + 1 < len(idx))]
    ev["before"] = idx[ev["p0"] - 1]
    ev["after"] = idx[ev["p0"] + 1]
    px = rcon.execute("""SELECT lineage, business_date, close_adj FROM adjusted_prices WHERE business_date >= ?
                         AND business_date <= ? AND lineage IN (SELECT UNNEST(?)) AND close_adj > 0""",
                      [S[0].date(), day, list(ev["lineage"].unique())]).df()
    px["business_date"] = pd.to_datetime(px["business_date"])
    P = px.pivot(index="business_date", columns="lineage", values="close_adj")
    mk = rcon.execute("""WITH r AS (SELECT business_date, ret_1d,
                                           ROW_NUMBER() OVER (PARTITION BY business_date ORDER BY adv20 DESC) AS rk
                                    FROM features WHERE business_date >= ? AND business_date <= ? AND adv20 > 0)
                         SELECT business_date, AVG(ret_1d) AS m FROM r WHERE rk <= 500 GROUP BY 1 ORDER BY 1""",
                      [S[0].date(), day]).df()
    mk["business_date"] = pd.to_datetime(mk["business_date"])
    cum = (1 + mk.set_index("business_date")["m"].fillna(0)).cumprod()
    out = []
    for r in ev.itertuples():
        try:
            c0, c1 = P.at[r.before, r.lineage], P.at[r.after, r.lineage]
            m = cum[r.after] / cum[r.before] - 1
        except KeyError:
            continue
        if np.isfinite(c0) and np.isfinite(c1):
            out.append({"lineage": r.lineage, "ann": r.ann, "after": r.after, "react": float(c1 / c0 - 1 - m)})
    return pd.DataFrame(out).drop_duplicates(["lineage", "after"])


def flagged(rcon, day: date, re: pd.DataFrame | None = None) -> dict[str, float]:
    """lineage -> reaction, for names flagged on ``day``: a bottom-fifth reaction whose window closed in the last
    ``HOLD`` sessions, judged against the reactions of the ``WINDOW_DAYS`` before it."""
    re = reactions(rcon, day) if re is None else re
    if re.empty:
        return {}
    sess = [pd.Timestamp(r[0]) for r in rcon.execute(
        "SELECT DISTINCT business_date FROM features WHERE business_date <= ? ORDER BY 1 DESC LIMIT ?", [day, HOLD]).fetchall()]
    recent = re[re["after"] >= min(sess)] if sess else re.iloc[0:0]
    out = {}
    for r in recent.itertuples():
        peers = re[(re["after"] <= r.after) & (re["after"] > r.after - pd.Timedelta(days=WINDOW_DAYS))]["react"]
        pct = float((peers < r.react).mean() + 0.5 * (peers == r.react).mean())     # mid-rank: ties sit in the middle
        if len(peers) >= 5 and pct <= QUANTILE:
            out[r.lineage] = min(out.get(r.lineage, 0.0), r.react)
    return out
