"""Selection regret: was the right opportunity chosen, not only did the chosen one make money.

For each decision day: the candidates that were on the list (taken and not taken), and what each did over the same
window. Three numbers:

    selection skill   mean of the chosen minus mean of all candidates - positive means the choice added value
    regret            the best candidate minus the mean of the chosen - what perfect hindsight would have added
    hit               the share of days the chosen mean beat the candidates' mean

A trader can make money on every trade and still be poor at choosing; this is where that shows.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def by_day(cands: pd.DataFrame) -> pd.DataFrame:
    """``cands``: day, ticker, chosen (bool), realised. One row per day."""
    rows = []
    for d, g in cands.dropna(subset=["realised"]).groupby("day"):
        ch = g[g["chosen"]]
        if ch.empty or len(g) < 2:
            continue
        best = g.loc[g["realised"].idxmax()]
        rows.append({"day": d, "chosen_mean": float(ch["realised"].mean()), "all_mean": float(g["realised"].mean()),
                     "best": float(best["realised"]), "best_ticker": best["ticker"],
                     "missed": None if bool(best["chosen"]) else best["ticker"], "candidates": int(len(g))})
    out = pd.DataFrame(rows)
    if len(out):
        out["skill"] = out["chosen_mean"] - out["all_mean"]
        out["regret"] = out["best"] - out["chosen_mean"]
        # pandas 3 stores a string column with missing values as NaN; "no miss" stays None on every version
        out["missed"] = pd.Series([x if isinstance(x, str) else None for x in out["missed"]], index=out.index, dtype=object)
    return out


def summary(days: pd.DataFrame) -> dict:
    if days.empty:
        return {"days": 0}
    s = days["skill"]
    t = float(s.mean() / (s.std(ddof=1) / np.sqrt(len(s)))) if len(s) > 2 and s.std(ddof=1) > 0 else float("nan")
    return {"days": int(len(days)), "skill": float(s.mean()), "skill_t": t, "regret": float(days["regret"].mean()),
            "hit": float((s > 0).mean())}


def book_candidates(book, rcon, version: str, mode: str = "replay", horizon: int | None = None) -> pd.DataFrame:
    """A paper book's candidates per signal day (positions + skips) and what each did from the next close."""
    pos = book.execute("SELECT signal_day, ticker, lineage, horizon FROM positions WHERE version = ? AND mode = ?",
                       [version, mode]).df().assign(chosen=True)
    sk = book.execute("""SELECT s.signal_day, s.ticker, p.lineage, NULL AS horizon FROM skips s
                         LEFT JOIN (SELECT DISTINCT ticker, lineage FROM positions) p USING (ticker)
                         WHERE s.version = ? AND s.mode = ?""", [version, mode]).df().assign(chosen=False)
    c = pd.concat([pos, sk], ignore_index=True).rename(columns={"signal_day": "day"})
    h = int(horizon or (pos["horizon"].mode().iloc[0] if len(pos) else 20))
    out = []
    for d, g in c.groupby("day"):
        lins = [x for x in g["lineage"].dropna().unique()]
        if not lins:
            continue
        px = rcon.execute("""SELECT lineage, business_date, close_adj FROM adjusted_prices WHERE lineage IN (SELECT UNNEST(?))
                             AND business_date > ? ORDER BY business_date""", [lins, d]).df()
        r = {}
        for lin, p in px.groupby("lineage"):
            v = p["close_adj"].to_numpy()
            if len(v) > h:
                r[lin] = v[h] / v[0] - 1
        g = g.assign(realised=g["lineage"].map(r))
        out.append(g)
    return pd.concat(out, ignore_index=True) if out else c.assign(realised=np.nan)


def journal_vs_list(trips: pd.DataFrame, lists: pd.DataFrame, rcon) -> pd.DataFrame:
    """For each of the owner's closed trips: the terminal's list on the evening before entry, what the best name on it
    did over the same days, and what the trip did - "you chose X; the list's best was Y, which did Z"."""
    out = []
    for t in trips.to_dict("records"):
        prior = lists[lists["day"] < str(t["entry_day"])]
        if prior.empty:
            continue
        day = prior["day"].max()
        names = list(prior[prior["day"] == day]["ticker"])
        px = rcon.execute("""SELECT ticker, business_date, close_adj FROM adjusted_prices WHERE ticker IN (SELECT UNNEST(?))
                             AND business_date IN (?, ?)""", [names, t["entry_day"], t["exit_day"]]).df()
        piv = px.pivot_table(index="ticker", columns="business_date", values="close_adj")
        if piv.shape[1] < 2:
            continue
        r = (piv.iloc[:, -1] / piv.iloc[:, 0] - 1).dropna()
        if r.empty:
            continue
        out.append({"symbol": t["symbol"], "entry_day": t["entry_day"], "exit_day": t["exit_day"], "trip": t["ret"],
                    "list_day": day, "list_best": r.idxmax(), "list_best_ret": float(r.max()), "list_mean": float(r.mean()),
                    "on_list": t["symbol"] in names})
    return pd.DataFrame(out)
