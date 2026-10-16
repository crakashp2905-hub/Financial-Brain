"""Does a stock's results reaction carry on into its promoter-group siblings?

Pre-registered in ``PRE-REGISTERED half-life and second-order spillover``. A second-order effect is the
claim that information about one company is not yet fully in the price of a related one when it is
public. The relation used is the dated promoter graph (``graph/promoter_dated``), live on the event's
entry date, so the study uses only links the archive had evidenced by then.

Event: a ``RESULTS`` filing for A. Signal: A's reaction ``ar_react``, already public by the first close
at or after publication. Outcome: each sibling's market-adjusted return from that same close over h
sessions. The effect for an event is ``sign(ar_react) x mean(sibling outcome)``; one number per event,
and the standard error is **clustered by entry date**, because results season stacks hundreds of events
onto the same few dates and treating them as independent would overstate t several-fold.

The placebo pairs each event with the siblings of a *different* event of the same year, measured over
this event's window. If a market-wide drift after results season were doing the work, the placebo would
show it.
"""
from __future__ import annotations

from datetime import timedelta

import numpy as np
import pandas as pd

LINK_TTL = 730
HORIZONS = (1, 5, 20)


class SpilloverError(ValueError):
    pass


def clustered_t(effect: pd.Series, dates: pd.Series) -> dict:
    """Mean effect and a t-statistic with one observation per entry date."""
    if len(effect) != len(dates):
        raise SpilloverError("effect and dates differ in length")
    d = pd.DataFrame({"e": effect.to_numpy(), "d": pd.to_datetime(dates).to_numpy()}).dropna()
    g = d.groupby("d")["e"].mean()
    if len(g) < 30:
        raise SpilloverError(f"only {len(g)} dates; below the 30 needed")
    return {"mean": float(d["e"].mean()), "t": float(g.mean() / (g.std(ddof=1) / np.sqrt(len(g)))),
            "events": int(len(d)), "dates": int(len(g))}


def live_siblings(links: pd.DataFrame, lineage: str, on, ttl: int = LINK_TTL) -> list[str]:
    """Lineages linked to ``lineage`` by a link evidenced on or before ``on`` and not yet lapsed."""
    on = pd.Timestamp(on)
    a = links[links["a"] == lineage]
    b = links[links["b"] == lineage]
    out = []
    for df, col in ((a, "b"), (b, "a")):
        m = df[(pd.to_datetime(df["first_seen"]) <= on)
               & (on <= pd.to_datetime(df["last_seen"]) + timedelta(days=ttl))]
        out += list(m[col])
    return sorted(set(out) - {lineage})


def run(events: pd.DataFrame, links: pd.DataFrame, prices: pd.DataFrame, *, horizons=HORIZONS,
        seed: int = 0) -> dict:
    """``events``: lineage, entry, ar_react. ``prices``: wide close_adj, index = date, columns = lineage."""
    rng = np.random.default_rng(seed)
    dates = list(prices.index)
    pos = {pd.Timestamp(d): i for i, d in enumerate(dates)}
    lp = np.log(prices)
    ev = events.dropna(subset=["ar_react"]).copy()
    ev = ev[ev["ar_react"] != 0]
    ev["entry"] = pd.to_datetime(ev["entry"])
    ev = ev[ev["entry"].isin(pos)]
    ev["sibs"] = [live_siblings(links, l, d) for l, d in zip(ev["lineage"], ev["entry"])]
    ev = ev[ev["sibs"].map(len) > 0].reset_index(drop=True)
    if len(ev) < 100:
        raise SpilloverError(f"only {len(ev)} events with live siblings")
    ev["year"] = ev["entry"].dt.year
    out = {}
    for h in horizons:
        real, plc = [], []
        for r in ev.itertuples():
            i = pos[r.entry]
            if i + h >= len(dates):
                real.append(np.nan)
                plc.append(np.nan)
                continue
            mkt = (lp.iloc[i + h] - lp.iloc[i]).median()

            def abn(names):
                cols = [c for c in names if c in lp.columns]
                if not cols:
                    return np.nan
                return float(((lp.iloc[i + h][cols] - lp.iloc[i][cols]) - mkt).mean())  # noqa: B023

            sgn = np.sign(r.ar_react)
            real.append(sgn * abn(r.sibs))
            pool = ev[(ev["year"] == r.year) & (ev.index != r.Index)]
            other = pool.iloc[int(rng.integers(len(pool)))]["sibs"] if len(pool) else []
            plc.append(sgn * abn(other))
        out[h] = {"real": clustered_t(pd.Series(real), ev["entry"]),
                  "placebo": clustered_t(pd.Series(plc), ev["entry"])}
    out["events"] = int(len(ev))
    return out
