"""A historical event -> price-response database, and "have we seen this before?".

Every exchange announcement in the liquid universe, with what the stock did around it, stored once so
that a new event can be asked about its analogues instead of being reasoned about from first
principles. Returns are market-adjusted (minus the universe median over the same window) and measured
from the **first close at or after publication**, so ``ar_react`` is the move already made by the time
anyone could trade and the ``ar*`` columns are what was still available afterwards.

An analogue query is point-in-time by construction: an event only counts as an analogue if its
*outcome window had closed* before the as-of date, so asking on 2020-03-01 never sees March 2020.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from . import news_model as N

TABLE = "event_responses"
COLUMNS = ("news_id", "isin", "event_type", "entry", "ar_react", "ar1", "ar3", "ar5", "ar20",
           "vol60", "adv")
MIN_ANALOGUES = 30


class ResponseError(ValueError):
    pass


def build(con, *, names: int = 1000) -> int:
    """(Re)materialise ``event_responses``. Returns the row count."""
    df = N.load(con, names=names)
    out = df[list(COLUMNS)].copy()
    out["entry"] = pd.to_datetime(out["entry"]).dt.date
    con.register("er_df", out)
    con.execute(f"DROP TABLE IF EXISTS {TABLE}")
    con.execute(f"CREATE TABLE {TABLE} AS SELECT * FROM er_df")
    con.unregister("er_df")
    return con.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0]


def _bucket(x: float, edges: list[float]) -> int:
    return int(np.searchsorted(edges, x))


def analogues(con, *, event_type: str, as_of: date, vol60: float | None = None,
              adv: float | None = None, horizon: int = 5, min_n: int = MIN_ANALOGUES) -> dict:
    """The record of past events like this one, using only outcomes already observable on ``as_of``.

    Like means the same ``event_type`` and, when given, the same terciles of trailing volatility and
    turnover (a 2% reaction means something different in a 15%-vol name than a 45%-vol one). If the
    tercile-matched set is too small the query falls back to the event type alone and says so, rather
    than quoting statistics from a handful of cases.
    """
    col = {1: "ar1", 3: "ar3", 5: "ar5", 20: "ar20"}.get(horizon)
    if col is None:
        raise ResponseError(f"horizon {horizon} is not stored; have {N.HORIZONS}")
    # The outcome window must be closed: entry + horizon sessions <= as_of. Calendar days stand in for
    # sessions, so the margin is deliberately generous (2x + 4).
    cutoff = as_of - pd.Timedelta(days=2 * horizon + 4)
    base = con.execute(f"""
        SELECT {col} AS y, ar_react, vol60, adv FROM {TABLE}
        WHERE event_type = ? AND entry <= ? AND {col} IS NOT NULL""",
                       [event_type, cutoff]).df()
    if base.empty:
        raise ResponseError(f"no resolved {event_type!r} events before {as_of}")
    matched, basis = base, "event type"
    if vol60 is not None and adv is not None:
        ve = list(base["vol60"].quantile([1 / 3, 2 / 3]))
        ae = list(base["adv"].quantile([1 / 3, 2 / 3]))
        m = base[(base["vol60"].map(lambda v: _bucket(v, ve)) == _bucket(vol60, ve))
                 & (base["adv"].map(lambda v: _bucket(v, ae)) == _bucket(adv, ae))]
        if len(m) >= min_n:
            matched, basis = m, "event type + volatility tercile + turnover tercile"
    y = matched["y"].to_numpy()
    if len(y) < min_n:
        raise ResponseError(f"only {len(y)} analogues; below the {min_n} needed to say anything")
    return {"event_type": event_type, "as_of": as_of, "horizon": horizon, "matched_on": basis,
            "n": int(len(y)), "mean": float(y.mean()), "median": float(np.median(y)),
            "hit_rate": float((y > 0).mean()),
            "p10": float(np.quantile(y, .1)), "p90": float(np.quantile(y, .9)),
            "typical_reaction": float(np.median(np.abs(matched["ar_react"]))),
            "se_mean": float(y.std(ddof=1) / np.sqrt(len(y)))}
