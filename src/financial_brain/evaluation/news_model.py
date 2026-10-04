"""A text classifier on exchange-filing headlines, scored against what the stock did afterwards.

Pre-registered in ``PRE-REGISTERED news classifier on filings``. Targets use only returns *after* the
first close at or after publication, so nothing here needs a price the filing could not have been
traded at.
"""
from __future__ import annotations

import re
from datetime import date, time

import numpy as np
import pandas as pd

CLOSE = time(15, 30)
HORIZON = 3
#: Forward horizons stored per event. ar3 is the registered target; the others are for analogues.
HORIZONS = (1, 3, 5, 20)
TRAIN_END, VALID_END = date(2021, 12, 31), date(2022, 12, 31)


class NewsModelError(ValueError):
    pass


def clean(headline: str, company: str | None) -> str:
    """Lowercase, digits -> 0, and the company's own name removed so identity cannot be learned."""
    t = (headline or "").lower()
    for part in re.split(r"\W+", (company or "").lower()):
        if len(part) > 2:
            t = re.sub(rf"\b{re.escape(part)}\b", " ", t)
    return re.sub(r"\s+", " ", re.sub(r"\d", "0", t)).strip()


def entry_positions(sessions: list[date], on: list[date], after_close: list[bool]) -> np.ndarray:
    """Index of the first session at or after each announcement date; after the close, the next one."""
    ords = np.array([d.toordinal() for d in sessions])
    pos = np.searchsorted(ords, np.array([d.toordinal() for d in on]))
    return pos + np.array(after_close, dtype=int)


def load(con, *, names: int = 1000, start=date(2016, 4, 1), end=date(2026, 6, 30)) -> pd.DataFrame:
    px = con.execute("""
        WITH u AS (SELECT isin FROM adjusted_prices WHERE business_date >= DATE '2025-01-01'
                   GROUP BY 1 ORDER BY AVG(turnover) DESC LIMIT ?)
        SELECT business_date, isin, close_adj FROM adjusted_prices
        WHERE isin IN (SELECT isin FROM u) AND close_adj > 0 AND business_date BETWEEN ? AND ?
    """, [names, start, end]).df()
    L = np.log(px.pivot_table(index="business_date", columns="isin", values="close_adj"))
    dates = list(L.index)
    daily = L.diff()
    M = np.nancumsum(np.nanmedian(daily.to_numpy(), axis=1))      # cumulative market log return
    vol = daily.rolling(60, min_periods=40).std().to_numpy()
    adv = con.execute("""SELECT isin, AVG(turnover) FROM adjusted_prices
                         WHERE business_date >= DATE '2025-01-01' GROUP BY 1""").df()
    adv = dict(zip(adv["isin"], adv.iloc[:, 1]))
    col = {c: j for j, c in enumerate(L.columns)}
    ann = con.execute("""
        SELECT news_id, isin, company, headline, event_type, business_date, published_at
        FROM announcements
        WHERE headline IS NOT NULL AND LENGTH(headline) > 20 AND business_date BETWEEN ? AND ?
    """, [start, end]).df()
    ann = ann[ann["isin"].isin(col)].copy()
    after = [t is not None and t >= CLOSE for t in ann["published_at"].dt.time]
    pos = entry_positions(dates, list(ann["business_date"]), after)
    j = ann["isin"].map(col).to_numpy()
    ok = (pos >= 1) & (pos + HORIZON < len(dates))
    pos, j, ann = pos[ok], j[ok], ann[ok].copy()
    Lv = L.to_numpy()
    ann["entry"] = [dates[p] for p in pos]
    ann["ar_react"] = (Lv[pos, j] - Lv[pos - 1, j]) - (M[pos] - M[pos - 1])
    ann["ar3"] = (Lv[pos + HORIZON, j] - Lv[pos, j]) - (M[pos + HORIZON] - M[pos])
    n_sess = len(dates)
    for h in HORIZONS:
        if h == HORIZON:
            continue
        reach = np.minimum(pos + h, n_sess - 1)        # events too close to the end stay NaN
        v = (Lv[reach, j] - Lv[pos, j]) - (M[reach] - M[pos])
        ann[f"ar{h}"] = np.where(pos + h < n_sess, v, np.nan)
    ann["vol60"] = vol[pos - 1, j]
    ann["adv"] = np.log(ann["isin"].map(adv).astype(float))
    ann = ann.dropna(subset=["ar3", "ar_react", "vol60", "adv"])  # extra horizons may stay NaN
    ann["text"] = [clean(h, c) for h, c in zip(ann["headline"], ann["company"])]
    return ann.reset_index(drop=True)


def split(df: pd.DataFrame):
    e = pd.to_datetime(df["entry"])
    return (df[e <= pd.Timestamp(TRAIN_END)].copy(), df[(e > pd.Timestamp(TRAIN_END)) & (e <= pd.Timestamp(VALID_END))].copy(),
            df[e > pd.Timestamp(VALID_END)].copy())


def date_clustered_spread(df: pd.DataFrame, score: str, y: str = "ar3", q: float = 0.1) -> dict:
    """Top-quantile minus bottom-quantile mean outcome, one observation per entry date."""
    hi, lo = df[score].quantile(1 - q), df[score].quantile(q)
    top = df[df[score] >= hi].groupby("entry")[y].mean()
    bot = df[df[score] <= lo].groupby("entry")[y].mean()
    d = (top - bot).dropna()
    if len(d) < 30:
        raise NewsModelError(f"only {len(d)} dates have both tails")
    return {"spread": float(d.mean()), "t": float(d.mean() / (d.std(ddof=1) / np.sqrt(len(d)))),
            "dates": int(len(d)), "top": float(top.mean()), "bottom": float(bot.mean())}
