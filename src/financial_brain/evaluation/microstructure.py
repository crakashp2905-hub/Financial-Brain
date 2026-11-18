"""Order-book and open-interest features from the intraday archive, defined once and tested.

An order-flow or open-interest hypothesis (candidates L14 and L20 of the intraday V2 list) cannot be registered until
the archive holds enough sessions (``ingest.archive_health.readiness``). What can be done now, without spending a trial,
is to fix the *definitions* so a later registration points at code that already exists and is unit-tested, and to use
the same numbers for what is useful today: how wide the spread really is by name and time of day (the cost side of every
intraday rule, which the V2 trial showed decides the result).

All features use only the snapshot at the poll and earlier polls of the same symbol, never later ones.

    spread_bp        (ask - bid) / mid, in basis points, from the best bid and ask
    imb_top          (bid_qty - ask_qty) / (bid_qty + ask_qty) at the best level            in [-1, 1]
    imb_depth        the same over the five levels the exchange shows                      in [-1, 1]
    dvol             volume traded since the previous poll (cumulative volume difference, never negative)
    doi              open-interest change since the previous poll (futures and options)
    ret              last-price return since the previous poll
    signed_flow      dvol signed by the return of the poll (a crude tick rule; 0 when the price did not move)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

COLUMNS = ("spread_bp", "imb_top", "imb_depth", "dvol", "doi", "ret", "signed_flow")


def _imbalance(b: pd.Series, a: pd.Series) -> pd.Series:
    tot = b + a
    return ((b - a) / tot).where(tot > 0)


def features(q: pd.DataFrame) -> pd.DataFrame:
    """``q``: rows of the ``quotes`` table (ts, symbol, ltp, volume, oi, bid, ask, bid_qty, ask_qty, depth_bid_qty,
    depth_ask_qty). Returns the same index with the feature columns added; rows are sorted by symbol and time."""
    d = q.sort_values(["symbol", "ts"]).copy()
    valid = (d["bid"] > 0) & (d["ask"] > 0) & (d["ask"] >= d["bid"])
    mid = (d["bid"] + d["ask"]) / 2
    d["spread_bp"] = ((d["ask"] - d["bid"]) / mid * 1e4).where(valid)
    d["imb_top"] = _imbalance(d["bid_qty"], d["ask_qty"]).where(valid)
    d["imb_depth"] = _imbalance(d["depth_bid_qty"], d["depth_ask_qty"]).where(valid)
    g = d.groupby("symbol", sort=False)
    d["dvol"] = g["volume"].diff().clip(lower=0)
    d["doi"] = g["oi"].diff()
    d["ret"] = g["ltp"].pct_change(fill_method=None)
    d["signed_flow"] = d["dvol"] * np.sign(d["ret"]).fillna(0)
    return d


def load(con, day: str, symbols: list[str] | None = None) -> pd.DataFrame:
    """One session's snapshots from an archive connection."""
    where = "CAST(ts AS DATE) = CAST(? AS DATE)"
    params: list = [day]
    if symbols:
        where += " AND symbol IN (" + ",".join("?" * len(symbols)) + ")"
        params += list(symbols)
    return con.execute(f"SELECT * FROM quotes WHERE {where} ORDER BY symbol, ts", params).df()


def spread_by_time(f: pd.DataFrame, minutes: int = 30) -> pd.DataFrame:
    """Median spread (bp) by symbol and clock bucket: the cost an intraday rule actually pays, by name and time of day."""
    x = f.dropna(subset=["spread_bp"]).copy()
    x["bucket"] = pd.to_datetime(x["ts"]).dt.floor(f"{minutes}min").dt.strftime("%H:%M")
    return x.groupby(["symbol", "bucket"])["spread_bp"].median().unstack("bucket")


def forward_return(f: pd.DataFrame, horizon: int = 5) -> pd.Series:
    """The return over the next ``horizon`` polls of the same symbol. For research on a *future* registration only; it
    looks ahead by construction and must never be used as a feature."""
    return f.groupby("symbol", sort=False)["ltp"].transform(lambda s: s.shift(-horizon) / s - 1)
