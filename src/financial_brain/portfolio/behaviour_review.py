"""The owner's habits, read from their own trades - and an anti-playbook of what keeps losing after costs.

Five detectors, each a fixed rule (nothing fitted to the owner's results):

    overtrading         a day with more than ``MAX_TRADES_DAY`` fills, or a round trip closed the same day at a loss
    averaging_down      a buy in a name already held at a loss (the buy price below the open lots' average cost by
                        more than ``AVG_DOWN_GAP``)
    premature_exit      a winning trip sold, after which the stock rose another ``RUN_ON`` within ``RUN_DAYS``
                        sessions
    event_chasing       a buy on a session where the name had already moved ``CHASE_MOVE`` or more, or on/after a
                        high-materiality filing that session
    ignored_invalidation a long held ``GRACE`` sessions or more after a close below the plan's own stop (the position
                        style: 2.5 x daily volatility x sqrt(20), volatility from the 60 sessions before entry)

The **anti-playbook** groups closed trips by tag and keeps a tag only when it has at least ``MIN_TRIPS_TAG`` trips,
a negative mean net return and t <= -2 - with the number of tags examined printed beside it, because with enough
groups one will look bad by chance. An entry is a warning to the owner, never a rule the system applies.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

MAX_TRADES_DAY = 10
AVG_DOWN_GAP = 0.03
RUN_ON, RUN_DAYS = 0.05, 20
CHASE_MOVE = 0.05
GRACE = 3
MIN_TRIPS_TAG = 8
STOP_K, STOP_WINDOW = 2.5, 20


def _t(x: pd.Series) -> float:
    x = x.dropna()
    return float(x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))) if len(x) > 2 and x.std(ddof=1) > 0 else float("nan")


def overtrading_days(trades: pd.DataFrame) -> list[str]:
    n = trades.groupby("day").size()
    return [str(d) for d, k in n.items() if k > MAX_TRADES_DAY]


def averaging_down(trades: pd.DataFrame) -> list[dict]:
    """Buys that add to a losing long: price below the open lots' average cost by more than ``AVG_DOWN_GAP``."""
    out = []
    for sym, g in trades.sort_values(["ts", "trade_id"]).groupby("tradingsymbol"):
        qty, cost = 0.0, 0.0
        for r in g.itertuples():
            q, px = float(r.quantity), float(r.price)
            if r.side == "buy":
                if qty > 0 and px < (cost / qty) * (1 - AVG_DOWN_GAP):
                    out.append({"symbol": sym, "day": str(r.day), "price": px, "avg_cost": cost / qty})
                qty, cost = qty + q, cost + q * px
            else:
                if qty > 0:
                    cost -= min(q, qty) * (cost / qty)
                qty = max(qty - q, 0.0)
    return out


def tag_trips(trips: pd.DataFrame, closes, *, events: dict | None = None) -> pd.DataFrame:
    """Each closed trip with its behaviour tags. ``closes(symbol)`` returns that name's daily close series (a
    DatetimeIndex); ``events`` maps symbol -> set of dates with a high-materiality filing."""
    events = events or {}
    tags = []
    for r in trips.itertuples():
        t = []
        c = closes(r.symbol)
        e0, e1 = pd.Timestamp(r.entry_day), pd.Timestamp(r.exit_day)
        if r.side == "long" and c is not None and len(c):
            before = c[c.index < e0]
            if len(before) >= 2 and abs(before.iloc[-1] / before.iloc[-2] - 1) >= CHASE_MOVE:
                t.append("event_chasing")
            elif pd.Timestamp(r.entry_day).date() in events.get(r.symbol, set()):
                t.append("event_chasing")
            if len(before) >= 61:
                vol = before.pct_change().tail(60).std()
                stop = r.entry_px * (1 - STOP_K * vol * np.sqrt(STOP_WINDOW))
                held = c[(c.index > e0) & (c.index <= e1)]
                below = np.flatnonzero(held.to_numpy() < stop)
                if len(below) and len(held) - 1 - below[0] >= GRACE:
                    t.append("ignored_invalidation")
            after = c[c.index > e1].head(RUN_DAYS)
            if r.ret > 0 and len(after) and after.max() / r.exit_px - 1 >= RUN_ON:
                t.append("premature_exit")
        if e0 == e1 and r.ret < 0:
            t.append("overtrading")
        tags.append(t)
    return trips.assign(tags=tags)


def anti_playbook(tagged: pd.DataFrame, col: str = "net") -> dict:
    """Tags that keep losing after costs. Returns the entries and how many tags were examined."""
    rows = tagged.explode("tags").dropna(subset=["tags"])
    examined = sorted(rows["tags"].unique()) if len(rows) else []
    out = []
    for tag, g in rows.groupby("tags"):
        x = g[col]
        t = _t(x)
        if len(x) >= MIN_TRIPS_TAG and x.mean() < 0 and np.isfinite(t) and t <= -2:
            out.append({"tag": tag, "trips": len(x), "mean_net": float(x.mean()), "t": t})
    return {"examined": len(examined), "entries": out,
            "note": f"{len(examined)} tags examined: at t <= -2 about {0.025 * len(examined):.1f} would flag by chance"}


def review(trades: pd.DataFrame, trips: pd.DataFrame, closes, *, events: dict | None = None) -> dict:
    tagged = tag_trips(trips, closes, events=events) if len(trips) else trips.assign(tags=[])
    if len(tagged) and "net" not in tagged:
        tagged = tagged.assign(net=tagged["ret"])
    counts = tagged.explode("tags")["tags"].value_counts().to_dict() if len(tagged) else {}
    return {"overtrading_days": overtrading_days(trades), "averaging_down": averaging_down(trades),
            "tag_counts": counts, "anti_playbook": anti_playbook(tagged) if len(tagged) else {"examined": 0, "entries": []},
            "tagged": tagged}
